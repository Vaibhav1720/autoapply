# Publishing to Chrome Web Store

## Critical: do NOT include `key` in manifest.json

The Web Store listing ID is fixed at first publish:

**Extension ID:** `anjgpjhdecnibcbogkclafanemofndea`

If you add a `key` field that maps to a *different* ID (e.g. unpacked dev ID
`npihfaencligidfaanfcffknffgaiead`), upload fails with:

> key field value in the manifest doesn't match the current item.

**For store uploads:** `manifest.json` must have **no** `key` field.

**For local unpacked dev** with a stable ID: temporarily add `key` back only
on your machine — never zip that build for the Web Store.

## OAuth redirect URI (Google Cloud Console)

Use the **store** extension ID:

```
https://anjgpjhdecnibcbogkclafanemofndea.chromiumapp.org/
```

## Build ZIP (macOS)

**Recommended (minimal v1.6.15 → API default fix, manifest 1.16.1):**

```bash
./tools/build-chrome-store-zip.sh
# → release/applyright-chrome-store-1.16.1.zip
```

Step-by-step upload: `extension/CHROME_WEB_STORE_UPLOAD.md`.

**From current `extension/` folder (includes local changes):**

```bash
./tools/build-extension-store-zip.sh
```

Do **not** include `key` in manifest for Web Store uploads.

Upload: https://chrome.google.com/webstore/devconsole/387a263d-7b49-4653-a127-ca79a97c74e0/anjgpjhdecnibcbogkclafanemofndea/edit
