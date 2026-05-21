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

```bash
cd extension
zip -r ../autoapply-extension-v1.15.0.zip manifest.json content.js background.js popup.js popup.html options.js options.html icons _locales
unzip -l ../autoapply-extension-v1.15.0.zip | head -20
```

**v1.15.0:** Narrow `host_permissions` to ATS domains + `optional_host_permissions` for custom career sites (Chrome Web Store “broad host permissions” policy). Do **not** include `key` in manifest.

Upload: https://chrome.google.com/webstore/devconsole/387a263d-7b49-4653-a127-ca79a97c74e0/anjgpjhdecnibcbogkclafanemofndea/edit
