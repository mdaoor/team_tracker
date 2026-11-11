//! Pure helpers of the desktop shell (no Tauri types, so `cargo test` covers them): what the sidecar answered -> what the OS should show.
//! The policy (when to nudge, once only) lives in Python (`tt_client.personal.notify`); this file only reads the answers.

use serde_json::Value;

/// One OS notification, as the `NotificationPort` of the sidecar (`personal_notify`) shaped it.
#[derive(Debug, PartialEq, Eq, Clone)]
pub struct Notice {
    pub key: String,
    pub title: String,
    pub body: String,
}

/// `{"notifications": [{"key","title","body","actions"}]}` -> notices. Malformed entries are skipped, never fatal: a nudge must not
/// take the shell down.
pub fn parse_notifications(answer: &Value) -> Vec<Notice> {
    answer
        .get("notifications")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|n| {
                    Some(Notice {
                        key: n.get("key")?.as_str()?.to_owned(),
                        title: n.get("title")?.as_str()?.to_owned(),
                        body: n
                            .get("body")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_owned(),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `{"timer": {"title","elapsed_min"} | null}` -> the tray line: "▶ reading · 12 min" or "No timer running".
pub fn timer_label(answer: &Value) -> String {
    match answer.get("timer").filter(|t| !t.is_null()) {
        None => "No timer running".to_owned(),
        Some(t) => {
            let title = t.get("title").and_then(Value::as_str).unwrap_or("timer");
            let m = t
                .get("elapsed_min")
                .and_then(Value::as_i64)
                .unwrap_or(0)
                .max(0);
            let spent = if m >= 60 {
                format!("{} h {:02} min", m / 60, m % 60)
            } else {
                format!("{m} min")
            };
            format!("▶ {title} · {spent}")
        }
    }
}

/// Did `personal_cal_tick` sync anything, and did any of it fail? (`added`, `error`) summed over the reports that ran.
pub fn calendar_outcome(answer: &Value) -> (u64, Vec<String>) {
    let mut added = 0;
    let mut errors = Vec::new();
    for r in answer
        .get("reports")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        added += r.get("added").and_then(Value::as_u64).unwrap_or(0);
        if let Some(e) = r.get("error").and_then(Value::as_str) {
            errors.push(format!(
                "{}: {e}",
                r.get("provider")
                    .and_then(Value::as_str)
                    .unwrap_or("calendar")
            ));
        }
    }
    (added, errors)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn notifications_are_read_and_bad_entries_skipped() {
        let a = json!({"notifications": [
            {"key": "ready:1", "title": "Sprint planning ended", "body": "Send 1 h to WEB?", "actions": ["tray send 1"]},
            {"title": "no key"},
            {"key": "eod:2026-10-23", "title": "How did today go?"}
        ]});
        assert_eq!(
            parse_notifications(&a),
            vec![
                Notice {
                    key: "ready:1".into(),
                    title: "Sprint planning ended".into(),
                    body: "Send 1 h to WEB?".into()
                },
                Notice {
                    key: "eod:2026-10-23".into(),
                    title: "How did today go?".into(),
                    body: "".into()
                },
            ]
        );
        assert!(parse_notifications(&json!({"error": {"code": "x"}})).is_empty());
        assert!(parse_notifications(&json!(null)).is_empty());
    }

    #[test]
    fn the_timer_label_reads_minutes_and_hours() {
        assert_eq!(timer_label(&json!({"timer": null})), "No timer running");
        assert_eq!(timer_label(&json!({})), "No timer running");
        assert_eq!(
            timer_label(&json!({"timer": {"title": "reading", "elapsed_min": 12}})),
            "▶ reading · 12 min"
        );
        assert_eq!(
            timer_label(&json!({"timer": {"title": "deep work", "elapsed_min": 75}})),
            "▶ deep work · 1 h 15 min"
        );
    }

    #[test]
    fn calendar_outcomes_sum_and_name_the_failing_provider() {
        let a = json!({"configured": true, "reports": [
            {"provider": "google", "added": 3, "error": null},
            {"provider": "microsoft", "added": 0, "error": "connect the calendar again"}
        ]});
        assert_eq!(
            calendar_outcome(&a),
            (3, vec!["microsoft: connect the calendar again".to_owned()])
        );
        assert_eq!(
            calendar_outcome(&json!({"configured": false, "reports": []})),
            (0, vec![])
        );
    }
}
