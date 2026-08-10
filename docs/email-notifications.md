# Email notifications

Tempa can send a best-effort SMTP email only when a workflow needs a person. Dashboard →
Settings → Notifications tab → Email alerts provides presets for Gmail and Microsoft 365,
plus Custom SMTP.

For Gmail, enable 2-Step Verification, then [open Google App Passwords](https://myaccount.google.com/apppasswords),
enter **Tempa** as the app name, and copy the generated password into Tempa. Use it instead of
your normal Google password. For Microsoft 365, [open Security info](https://mysignins.microsoft.com/security-info),
choose **Add method** → **App password**, name it **Tempa**, and copy the generated password.
If App password is unavailable, ask the administrator to enable it and Authenticated SMTP.

The dashboard can save SMTP credentials locally in the workspace configuration. If you prefer
not to store them there, leave those fields blank and provide credentials through environment
variables instead:

```text
TEMPA_SMTP_USERNAME
TEMPA_SMTP_PASSWORD
```

Use **Send Test Email** in the dashboard or:

```text
tempa notifications test
```

Emails are sent for authentication failures, permanent implementation/planning/clarification
failures, run-limit safeguards, unanswered critical/major clarification findings, verification
failures, and the interactive "run another clarification round" prompt. They do not approve
actions; return to the dashboard or CLI to continue.

Tempa intentionally sends no email for automatic recovery: usage-limit waits, provider retries,
resumed implementation/QA sessions, automatic QA fixes, or successful completion.

Events are persisted in `<workspace>/.tempa/notification-outbox.json` before delivery. An SMTP
failure does not change Tempa's process result; the next Tempa command/dashboard start retries
pending delivery once. Repeated observations of the same state are deduplicated.
