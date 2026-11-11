//! The desktop shell around the web views: the tray icon (running timer, open, quick add, sync now, quit), OS notifications, the global
//! hotkey, close-to-tray, and the background loop that keeps calendars in sync. No business logic: what to say and when lives in Python
//! (`tt_client.personal.notify`, `tt_client.integrations.sync.Scheduler`); this file asks the sidecar once a minute and shows the answer.

use std::time::Duration;

use serde_json::{json, Value};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, Wry,
};
use tauri_plugin_global_shortcut::ShortcutState;
use tauri_plugin_notification::NotificationExt;

use crate::notice::{calendar_outcome, parse_notifications, timer_label, Notice};

const TRAY_ID: &str = "main";
/// One sidecar call per minute: the Python `Scheduler` decides which calendars are due, `notify_due` which nudges are new.
const TICK: Duration = Duration::from_secs(60);
pub const HOTKEY: &str = "CommandOrControl+Shift+Space";

/// The "timer" line of the tray menu, kept so the background loop can update it.
struct TimerItem(MenuItem<Wry>);

pub fn show_personal(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("personal") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn toggle_palette(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("palette") {
        if w.is_visible().unwrap_or(false) {
            let _ = w.hide();
        } else {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}

/// The global-shortcut plugin with the palette hotkey wired to the palette window (the scaffold registered the plugin only).
pub fn hotkey_plugin() -> tauri::plugin::TauriPlugin<Wry> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_shortcut(HOTKEY)
        .expect("the palette hotkey is a valid accelerator")
        .with_handler(|app, _shortcut, event| {
            if event.state == ShortcutState::Pressed {
                toggle_palette(app);
            }
        })
        .build()
}

/// Build the tray icon and its menu. Left click opens "My time".
pub fn install_tray(app: &tauri::App) -> tauri::Result<()> {
    let timer = MenuItem::with_id(app, "timer", "No timer running", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open My time", true, None::<&str>)?;
    let quick = MenuItem::with_id(app, "quick", "Quick add…", true, None::<&str>)?;
    let sync = MenuItem::with_id(app, "sync", "Sync calendars now", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Team Tracker", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&timer, &open, &quick, &sync, &quit])?;
    app.manage(TimerItem(timer));

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("Team Tracker")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_personal(app),
            "quick" => toggle_palette(app),
            "sync" => {
                let app = app.clone();
                tauri::async_runtime::spawn(async move { background_step(&app, true).await });
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_personal(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// Ask for notification permission once (a no-op where the OS needs none).
pub fn request_notification_permission(app: &AppHandle) {
    let _ = app.notification().request_permission();
}

fn show(app: &AppHandle, n: &Notice) {
    if let Err(e) = app
        .notification()
        .builder()
        .title(&n.title)
        .body(&n.body)
        .show()
    {
        eprintln!("notification {} not shown: {e}", n.key);
    }
}

fn set_timer_line(app: &AppHandle, label: &str) {
    if let Some(item) = app.try_state::<TimerItem>() {
        let _ = item.0.set_text(label);
    }
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(&format!("Team Tracker · {label}")));
    }
}

/// One background step: `personal_shell_tick` (due calendar syncs + new notifications + the running timer) in a single sidecar call.
/// `now` forces a calendar sync (the tray's "Sync calendars now") through `personal_cal_sync`.
pub async fn background_step(app: &AppHandle, now: bool) {
    if now {
        match crate::sidecar(app, "personal_cal_sync", json!({})).await {
            Ok(_) => {
                let _ = app.emit("personal-refresh", ());
            }
            Err(e) => eprintln!("calendar sync failed: {e}"),
        }
    }
    let answer: Value = match crate::sidecar(app, "personal_shell_tick", json!({})).await {
        Ok(a) => a,
        Err(e) => {
            eprintln!("shell tick failed: {e}");
            return;
        }
    };
    let (added, errors) = calendar_outcome(answer.get("calendar").unwrap_or(&Value::Null));
    for e in &errors {
        eprintln!("calendar: {e}");
    }
    let notices = parse_notifications(&answer);
    for n in &notices {
        show(app, n);
    }
    set_timer_line(app, &timer_label(&answer));
    if added > 0 || !notices.is_empty() {
        let _ = app.emit("personal-refresh", ()); // the open window reloads its lists
    }
}

/// Start the loop. The first step runs a few seconds after launch so a cold start does not delay the window.
pub fn spawn_background(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(5)).await;
        loop {
            background_step(&app, false).await;
            tokio::time::sleep(TICK).await;
        }
    });
}

/// Closing "My time" hides it: the tray keeps the timer, the sync and the nudges running. Quit is in the tray menu.
pub fn keep_running_in_tray(window: &tauri::Window, event: &tauri::WindowEvent) {
    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "personal" {
            api.prevent_close();
            let _ = window.hide();
        }
    }
}
