# Chrome Web Store Resubmission Checklist

Listing URL: https://chrome.google.com/webstore/devconsole/387a263d-7b49-4653-a127-ca79a97c74e0/anjgpjhdecnibcbogkclafanemofndea/edit

## ✅ Already fixed in code

- **Language**: `default_locale: "en"` declared in manifest + `_locales/en/messages.json` added.
- **Homepage URL**: `homepage_url` set in manifest.
- **Description**: name + description now sourced from i18n messages and clearly state what the extension does.
- **Keyword spam (Yellow Argon, ID FZSL — May 2026)**: removed the comma-separated brand list ("Greenhouse, Lever, Workday, Ashby, Workable, SmartRecruiters, iCIMS …") from the short description and from the long description. The new copy describes WHAT the extension does for the user, not which back-ends it talks to. Brand names are mentioned at most once and only where strictly necessary to justify a permission.

You must rebuild the extension ZIP after these manifest changes and upload it as a new package version (the manifest is now `1.6.17`).

---

## 📝 Dashboard fields you must complete

### 1. Detailed description (Store listing → "Description")

Paste this (clearly explains what it does + why to install — the rejection cited "excessive / irrelevant keywords", so this version describes the user benefit and avoids brand-name lists):

```
AutoApply saves you hours on every job hunt by filling out application
forms automatically using the information from your resume.

WHAT IT DOES
• Reads your resume once (PDF or DOCX) after a one-time Google sign-in.
• Detects the application form on the page and fills in name, email,
  phone, work history, education, skills, links and the common
  knock-out questions (work authorization, sponsorship, notice
  period, etc.).
• You stay in control: nothing is submitted automatically. You always
  review the filled form and click "Submit" yourself.

WHY INSTALL
• Stop retyping the same information into dozens of different
  application forms.
• Works on most modern career sites used by large and small employers.
• Your resume and profile are stored in your private account; the
  extension never sells or shares your data.

HOW TO USE
1. Install AutoApply and pin it to your toolbar.
2. Sign in with Google and upload your resume.
3. Open any job application page and click the AutoApply icon — the
   form is filled in seconds.

PERMISSIONS WE ASK FOR AND WHY
• activeTab / scripting: read and fill the form on the tab you are on.
• storage: remember your sign-in and preferences locally.
• identity: Google sign-in.
• Host permissions for common applicant tracking systems: needed to
  fill forms on those sites. We never read pages on unrelated sites.
```

### 2. Category
Select **Productivity**.

### 3. Language
Select **English** (matches the new `default_locale`).

### 4. Associated website
- Go to https://search.google.com/search-console
- Add and verify ownership of: `https://<your-static-web-app>.azurestaticapps.net`
  (or your production domain if different).
- Once verified, return to the Web Store listing and select that site
  from the "Associated website" dropdown.

> If you don't own a verifiable site yet, leave this blank — it is a
> recommendation, not a hard rejection. The hard rejection items are
> #1 (description), #5 (mature content) and #6 (privacy/single purpose).

### 5. Support / Description URLs
- **Support URL**: e.g. `https://<your-static-web-app>.azurestaticapps.net/support`
- **Homepage URL**: `https://<your-static-web-app>.azurestaticapps.net`

If you don't yet have a `/support` page, create one (even a simple
"Email us at techvibeapps.ai@gmail.com" page is enough).

### 6. Mature content declaration
Under **Privacy → Mature content** select **"My item does not contain mature content"**.
AutoApply has no sexual, violent, or substance content — just declare "None".

### 7. Single purpose
Field copy:
```
AutoApply has a single purpose: automatically fill out job application
forms using the user's stored resume data.
```

### 8. Permission justifications (each permission requires one)

| Permission | Justification |
|---|---|
| `activeTab` | Required to read the application form on the page the user is currently viewing so we can detect fields and fill them. |
| `scripting` | Required to inject the autofill script into the active tab when the user clicks the toolbar icon. |
| `storage` | Used to cache the user's sign-in token and autofill preferences locally on their device. |
| `identity` | Used for Google sign-in so the user can securely log in to their AutoApply account. |
| Host permissions for applicant tracking systems | Needed to detect and fill application forms on the small set of common ATS domains in `host_permissions`. The extension does not read or modify pages on any other site. |
| `optional_host_permissions <all_urls>` | Requested only on demand when the user explicitly clicks the AutoApply icon on a custom career site not in the bundled host list. We never inject without that user gesture. |

### 9. Privacy policy
A privacy policy URL is mandatory because the extension uses `identity`
and `storage`. Host a page at e.g.
`https://<your-static-web-app>.azurestaticapps.net/privacy` covering:
- What data is collected (Google email, resume contents, profile).
- Where it is stored (Azure Cosmos DB / Blob Storage).
- That data is never sold or shared with third parties.
- How users can delete their account and data.

---

## 📦 Repackage steps

```powershell
cd <repo-root>
Compress-Archive -Path extension\* -DestinationPath autoapply-extension-v1.6.17.zip -Force
```

Upload `autoapply-extension-v1.6.17.zip` as a new package, fill the
dashboard fields above, then submit for review.
