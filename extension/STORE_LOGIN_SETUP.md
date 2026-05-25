# Chrome Web Store login (v1.6.15) — no user workarounds

Users install **ApplyRight / AutoApply** from the Web Store (UI **v1.6.15**, package **1.16.0**).  
They should only: open the extension → **Sign in with Google**. No unpacked load, no manual API URL.

Everything below is **your** (operator) setup. The published `.crx` does not need to change for OAuth redirect fixes.

---

## 1. Google Cloud — OAuth redirect (fixes `redirect_uri_mismatch`)

The store extension ID is fixed:

**`anjgpjhdecnibcbogkclafanemofndea`**

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials).
2. Edit the **Web application** OAuth 2.0 client used by the product  
   Client ID: `8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com`
3. **Authorized redirect URIs** — add this line **exactly** (trailing slash required):

   ```
   https://anjgpjhdecnibcbogkclafanemofndea.chromiumapp.org/
   ```

4. **Authorized JavaScript origins** (for the website, not the extension) — ensure you have:

   ```
   https://autoapplynow.in
   https://www.autoapplynow.in
   https://mango-ocean-0f1de6810.2.azurestaticapps.net
   http://localhost:8080
   ```

5. Save. Wait 2–5 minutes for Google to propagate.

6. Also add the **same path without a trailing slash** (some Chrome builds differ):

   ```
   https://anjgpjhdecnibcbogkclafanemofndea.chromiumapp.org
   ```

**Do not** create a separate “Chrome extension” OAuth client type for this flow. The store build uses the **Web** client + `chrome.identity.launchWebAuthFlow`.

### Redirect already added but login still fails?

Check these in order (no extension update required):

| Check | What to verify |
|--------|----------------|
| **Same OAuth client** | The redirect URI must be on client **`8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1`**, not a second “Chrome extension” or “Desktop” client. The store `.crx` hard-codes this Web client ID in `popup.js`. |
| **OAuth consent screen** | [APIs & Services → OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent). If status is **Testing**, only emails listed under **Test users** can sign in — everyone else sees “Access blocked” / invalid request. Fix: **Publish app** to Production, or add each user’s Gmail under Test users. |
| **Install source** | Test with an install from the **Web Store** only. “Load unpacked” uses a **different** extension ID → different `chromiumapp.org` redirect → mismatch even if the store URI is registered. |
| **Exact error phase** | Fails on **Google’s page** → consent / client / redirect (above). Fails after account picker with **“Verifying…”** then **Failed to fetch** → API/network/ad-block (§3), not redirect. |
| **Implicit `id_token` flow** | Store v1.6.15 uses `response_type=id_token`. If Google disabled implicit grants on that Web client, login breaks until you ship a store update that uses **authorization `code` + PKCE** (same as the web app). |

---

## 2. Azure Functions — accept Google tokens (fixes “Invalid Google ID token” after Google OK)

The extension posts the Google **ID token** to:

`POST https://autoapply-func-dev.azurewebsites.net/api/v1/auth/google`

On the Function App **`autoapply-func-dev`**, set application settings:

| Setting | Value |
|--------|--------|
| `GOOGLE_CLIENT_IDS` | `8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com` |
| `JWT_SECRET` | Strong random secret (not the dev default) |
| `GOOGLE_CLIENT_SECRET` | Web client secret (needed for **web** PKCE login; optional for extension id_token path) |

```bash
az functionapp config appsettings set \
  -g rg-autoapply-dev \
  -n autoapply-func-dev \
  --settings \
    "GOOGLE_CLIENT_IDS=8017795829-np8cfekibbnfr960rfo6fqj6g8kl8dm1.apps.googleusercontent.com"
```

Verify:

```bash
bash tools/verify-chrome-store-login.sh
```

---

## 3. `Failed to fetch` after Google sign-in (most common regression)

The store build shows **✗ Failed to fetch** when the popup cannot complete  
`POST https://autoapply-func-dev.azurewebsites.net/api/v1/auth/google`  
(Google account picker already succeeded).

### Root cause (worked before, broke after CORS hardening)

If someone ran `tools/configure-function-cors.sh` with **only website origins**, Azure blocks browser/extension cross-origin POSTs unless the **extension origin** is allowlisted too.

**Fix (no new Chrome Web Store upload):** add this origin on the Function App:

```
chrome-extension://anjgpjhdecnibcbogkclafanemofndea
```

```bash
az functionapp cors add \
  -g rg-autoapply-dev \
  -n autoapply-func-dev \
  --allowed-origins "chrome-extension://anjgpjhdecnibcbogkclafanemofndea"

# Or re-run the script (now includes the extension origin):
bash tools/configure-function-cors.sh
```

Then have users **retry Sign in with Google** (no extension reinstall).

**Verify CORS is live** (must show extension origin on POST):

```bash
curl -sS -D - -o /dev/null -X POST "https://autoapply-func-dev.azurewebsites.net/api/v1/auth/google" \
  -H "Origin: chrome-extension://anjgpjhdecnibcbogkclafanemofndea" \
  -H "Content-Type: application/json" -d '{"idToken":"x"}' | grep -i access-control
```

Expect: `access-control-allow-origin: chrome-extension://anjgpjhdecnibcbogkclafanemofndea`

### If extension Google button still fails — use web login (no extension update)

1. In **normal Chrome** (not LinkedIn/in-app browser), open **https://autoapplynow.in**
2. Sign in with Google on the **website** (must reach the app / profile — not fail on web).
3. **Keep that tab open.** Wait 2–3 seconds.
4. Open the extension popup — it should show you as signed in (**do not** click extension Google again).
5. If still signed out: on autoapplynow.in press F12 → Console → run:  
   `document.documentElement.getAttribute('data-autoapply-ext')`  
   Should be `connected` after login. If `installed` only, reload the page once.

After deploying the latest web app, login also sends `AUTOAPPLY_SYNC_TOKEN` immediately (faster than the 2s poll).

### Also check

- `GET https://autoapply-func-dev.azurewebsites.net/api/v1/health` → **200**
- Extension **Settings → API URL** = `https://autoapply-func-dev.azurewebsites.net` (Reset if changed)
- Ad-blockers not blocking `*.azurewebsites.net`

### Profile “Check again” does nothing / extension not detected

Chrome often sets new extensions to **“On click”** — the extension **does not run on autoapplynow.in** until you click the puzzle icon on that tab.

Fix:

1. `chrome://extensions` → **ApplyRight** → **Details**
2. **Site access** → **On autoapplynow.in** (or **On all sites**)
3. **Reload** https://autoapplynow.in
4. Profile → **Check again** (should show Connected within a few seconds)

### Still “Failed to fetch” on extension Google sign-in

1. Do **not** use extension Google if it keeps failing.
2. Sign in on **https://autoapplynow.in** in normal Chrome (same browser).
3. Leave that tab open → open extension popup (token syncs from the page).
4. Optional: Extension **Settings** → try API URL `https://autoapplynow.in` then `https://autoapply-func-dev.azurewebsites.net`.

Long-term: link Function App to Static Web App (Standard SKU) so `/api/*` on autoapplynow.in hits the backend (free SKU returns HTML for `/api` today).

---

## 4. What users should have in extension Settings

Default API URL (built into the store build):

`https://autoapply-func-dev.azurewebsites.net`

If a user changed **Settings → API URL** to something else, login breaks. Tell them: open extension **Settings (gear)** → **Reset API URL to default**.

---

## 5. When you **must** publish a new store version

You **cannot** change the code inside the installed v1.6.15 package without a Web Store update.

Publish a new version only if you need:

- API calls via background worker (fewer `Failed to fetch` cases),
- New API hostname in `host_permissions`,
- UI/copy changes.

Until then, steps 1–3 are enough for **Sign in with Google** on the store build.

---

## 6. Quick test (as a user)

1. Install from [Chrome Web Store listing](https://chromewebstore.google.com/detail/autoapply-%E2%80%93-job-form-auto/anjgpjhdecnibcbogkclafanemofndea) (not Load unpacked).
2. Open extension → **Sign in with Google**.
3. Expect: Google account picker → brief “Verifying with backend…” → signed in.

If step 2 fails on Google’s page → fix **§1**.  
If Google succeeds but extension shows an HTTP error → fix **§2**.  
If message is `Failed to fetch` → fix **§3** (and ad-blockers).

---

## Reference

- Extension ID & redirect: `extension/PUBLISH.md`
- Print all OAuth URIs: `bash tools/print-extension-oauth-redirect.sh`
