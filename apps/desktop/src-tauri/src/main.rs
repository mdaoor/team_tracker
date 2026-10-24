// SCAFFOLD (unbuilt): a global hotkey toggles the palette window; every `tt_*` command forwards one JSON request to the `tt-sidecar`
// binary (tt_client built with PyInstaller, ADR-0038) and returns its JSON answer. No business logic lives in Rust.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, State};
use tauri_plugin_shell::ShellExt;

struct Hotkey(String);

async fn sidecar(app: &tauri::AppHandle, cmd: &str, args: serde_json::Value) -> Result<serde_json::Value, String> {
    let out = app
        .shell()
        .sidecar("tt-sidecar")
        .map_err(|e| e.to_string())?
        .args([cmd, &args.to_string()])
        .output()
        .await
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())
}

macro_rules! bridge {
    ($name:ident, $cmd:expr) => {
        #[tauri::command]
        async fn $name(app: tauri::AppHandle, args: Option<serde_json::Value>) -> Result<serde_json::Value, String> {
            sidecar(&app, $cmd, args.unwrap_or(serde_json::json!({}))).await
        }
    };
}
bridge!(tt_parse, "parse");
bridge!(tt_pack, "pack");
bridge!(tt_send, "send");
bridge!(tt_build_manual, "build-manual");
bridge!(tt_outbox, "outbox");
bridge!(tt_resend, "resend");
bridge!(tt_discard, "discard");
bridge!(tt_open_editor, "open-editor");
bridge!(tt_active, "active");

#[tauri::command]
fn hotkey(state: State<Hotkey>) -> String {
    state.0.clone()
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(Hotkey("CommandOrControl+Shift+Space".into()))
        .invoke_handler(tauri::generate_handler![
            tt_parse, tt_pack, tt_send, tt_build_manual, tt_outbox, tt_resend, tt_discard, tt_open_editor, tt_active, hotkey
        ])
        .setup(|app| {
            if let Some(w) = app.get_webview_window("palette") {
                let _ = w.hide();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Team Tracker");
}
