# Upload ApplyRight extension to Chrome Web Store (step by step)

**Listing ID (fixed):** `anjgpjhdecnibcbogkclafanemofndea`  
**This release:** manifest `1.16.1`, popup UI **v1.6.16** — same code as live **v1.6.15**, default API `https://autoapplynow.in` (no manual Settings step for new installs).

## 1. Build the ZIP on your Mac

From the repo root:

```bash
chmod +x tools/build-chrome-store-zip.sh
./tools/build-chrome-store-zip.sh
```

Output file (upload this):

```text
release/applyright-chrome-store-1.16.1.zip
```

**Do not** zip a folder that includes `key` in `manifest.json` (unpacked dev only).

## 2. Open the developer dashboard

1. Go to [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole).
2. Sign in with the Google account that owns the listing.
3. Open **ApplyRight** (or your extension name) — item ID `anjgpjhdecnibcbogkclafanemofndea`.

Direct edit link (if you use the same console project):

```text
https://chrome.google.com/webstore/devconsole/387a263d-7b49-4653-a127-ca79a97c74e0/anjgpjhdecnibcbogkclafanemofndea/edit
```

## 3. Upload the new package

1. Left menu: **Package** (or **Build** → **Package**).
2. Click **Upload new package**.
3. Select `release/applyright-chrome-store-1.16.1.zip`.
4. Wait for upload + automated checks (manifest, permissions, etc.).
5. Confirm **version** shows **1.16.1** (must be higher than the current published **1.16.0**).

If upload fails with *key field doesn't match*: you zipped a dev `manifest.json` that contains `"key"` — rebuild with the script above (uses `main` without `key`).

## 4. Store listing text (optional but recommended)

**Version name / release notes** (example):

```text
1.6.16 — Fix sign-in: extension now uses https://autoapplynow.in as the default API (no Settings change required).
```

You can keep the public version label aligned with **v1.6.16** in the popup.

## 5. Privacy & permissions

- If Chrome asks about **broad host permissions**, point reviewers to your existing justification (ATS domains + optional sites). This ZIP is the same permission set as 1.6.15 on `main`.
- **Privacy policy URL** should remain `https://autoapplynow.in` (or your current live policy URL).

## 6. Submit for review

1. Complete any required tabs (Privacy, Data use, etc.) if they show warnings.
2. Click **Submit for review** (or **Publish** if your console uses one-step publish).
3. Review usually takes from a few hours to a few business days.

## 7. After approval

- Users get the update automatically (Chrome may take hours to refresh).
- **Google OAuth:** no change — same extension ID, same redirect `https://anjgpjhdecnibcbogkclafanemofndea.chromiumapp.org/`.
- **Azure / API:** still `https://autoapplynow.in/api/*` on the server side (already configured).

## What changed vs Web Store v1.6.15 (1.16.0)

| File | Change |
|------|--------|
| `popup.js`, `options.js`, `background.js` | `DEFAULT_API_BASE` → `https://autoapplynow.in`; migrate stored legacy Azure URL |
| `popup.html` | Label v1.6.16; settings placeholder `autoapplynow.in` |
| `manifest.json` | `version` **1.16.1** (required bump) |

No new OAuth client, no new extension ID, no new host permissions in this minimal package.

## Troubleshooting upload

| Error | Fix |
|--------|-----|
| Version must be greater than … | Bump `"version"` in `manifest.json` and rebuild (e.g. 1.16.2). |
| key doesn't match | Remove `"key"` from manifest; rebuild ZIP. |
| Item ID mismatch | You must upload to the **existing** listing, not “New item”. |
