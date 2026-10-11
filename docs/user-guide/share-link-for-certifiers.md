# Share link for certifiers

A share link gives a certifier a read-only view of the signed package without an account.

## Create one

1. On the Review screen find "Certifier share link".
2. Choose the number of days (1 to 30) and an optional label.
3. Click "Create share link". The full link is shown once. Copy it now.

The secret is in the address fragment (after the #), so the browser never sends it to the server and it is not logged in server access logs.

## Manage links

The list shows each link's expiry and how many times it has been opened. Every opening is logged and sends a notification. Click "Revoke" to stop a link working at once.

## What the app will refuse

- Creating a link before Gate 3 is signed, or as a checker: only a designer or approver sees the option.
- A lifetime outside 1 to 30 days.
- Any opening of an expired or revoked link.
- Any change through the link: it is read only.
- A link after the project has been retired.
