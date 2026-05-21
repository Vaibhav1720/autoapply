# AutoApply Documentation Index

Complete guide to all setup and configuration documentation for AutoApply.

## 📍 START HERE

**New to the project?** Start with one of these based on your situation:

### I want to get running ASAP
→ Read: [QUICKSTART.md](QUICKSTART.md) (5-minute guide)

### I want step-by-step instructions
→ Read: [SETUP_LOCAL.md](SETUP_LOCAL.md) (20-minute guide)

### I want to understand the architecture first
→ Read: [README.md](README.md) (product overview) + [PROJECT_STATE.md](PROJECT_STATE.md) (codebase)

### I'm stuck or need help
→ Read: [VALIDATION.md](VALIDATION.md) (checklist to verify setup) + [SETUP_LOCAL.md#Troubleshooting](SETUP_LOCAL.md) (troubleshooting)

---

## 📚 Complete Documentation Map

### 🚀 Setup & Getting Started

| File | Purpose | Audience | Read Time |
|------|---------|----------|-----------|
| [QUICKSTART.md](QUICKSTART.md) | Minimal quick-start reference | Everyone | 5 min |
| [SETUP_LOCAL.md](SETUP_LOCAL.md) | Complete step-by-step setup guide | Developers | 20 min |
| [SETUP_READY.md](SETUP_READY.md) | Summary of what was created + checklist | Everyone | 10 min |
| [CONFIGURATION.md](CONFIGURATION.md) | Technical reference for all placeholders | Developers | 10 min |
| [SETUP.md](SETUP.md) | Original comprehensive guide (includes Azure provisioning) | DevOps/Architects | 30 min |

### ✅ Validation & Troubleshooting

| File | Purpose | Audience | Read Time |
|------|---------|----------|-----------|
| [VALIDATION.md](VALIDATION.md) | Setup verification checklist | Everyone | 15 min |
| [SETUP_LOCAL.md#Troubleshooting](SETUP_LOCAL.md#troubleshooting) | Common issues and fixes | Developers | 5-10 min |

### 📖 Reference & Architecture

| File | Purpose | Audience | Read Time |
|------|---------|----------|-----------|
| [README.md](README.md) | Product overview, API docs, FAQ | Everyone | 30 min |
| [PROJECT_STATE.md](PROJECT_STATE.md) | Codebase structure, schema, current state | Developers | 20 min |
| [docs/DESIGN_DOCUMENT.txt](docs/DESIGN_DOCUMENT.txt) | Original system design | Architects | 45 min |

### 🛠️ Automation & Tools

| File | Purpose | Audience | Read Time |
|------|---------|----------|-----------|
| [tools/setup-local-dev.ps1](tools/setup-local-dev.ps1) | Automated setup script | Everyone | (run it) |
| [api/local.settings.json.template](api/local.settings.json.template) | Backend config template | Developers | (reference) |

---

## 🎯 Common Tasks & Where to Find Them

### "I want to set up local development"
1. [QUICKSTART.md](QUICKSTART.md) — Read prerequisites
2. [SETUP.md](SETUP.md) § 1-2 — Provision Azure resources (one-time)
3. [SETUP_LOCAL.md](SETUP_LOCAL.md) § 1 — Run setup script
4. [VALIDATION.md](VALIDATION.md) — Verify setup works

### "I want to understand the code"
1. [README.md](README.md) § Product — Understand what it does
2. [PROJECT_STATE.md](PROJECT_STATE.md) § 2-4 — Learn codebase structure
3. [README.md](README.md) § 4 — Read API documentation
4. Browse `api/function_app.py`, `app/lib/`, `extension/` source files

### "Something's broken"
1. [VALIDATION.md](VALIDATION.md) — Run relevant checklist section
2. [SETUP_LOCAL.md](SETUP_LOCAL.md) § Troubleshooting — Find your error
3. Check browser console / terminal output for error messages
4. Verify credentials in `api/local.settings.json` match Azure resources

### "I want to deploy to Azure"
1. [SETUP.md](SETUP.md) § 5 — Deploy backend
2. [SETUP.md](SETUP.md) § 6 — Deploy frontend
3. [SETUP.md](SETUP.md) § 7 — Configure extension
4. [SETUP.md](SETUP.md) § 10 — Run smoke tests

### "I want to understand the configuration"
1. [CONFIGURATION.md](CONFIGURATION.md) — See all placeholders explained
2. [SETUP_LOCAL.md](SETUP_LOCAL.md) § Configuration Reference — Detailed table
3. [api/local.settings.json.template](api/local.settings.json.template) — Example values

### "I want to contribute to the codebase"
1. [PROJECT_STATE.md](PROJECT_STATE.md) — Understand current state
2. [README.md](README.md) § API Reference — See what routes exist
3. [docs/DESIGN_V2.txt](docs/DESIGN_V2.txt) — Latest design decisions
4. Browse relevant source files for the feature you're adding

---

## 📋 File Descriptions

### QUICKSTART.md
**The absolute minimum.** Reference card with just the commands you need. Good for experienced developers who've set up similar projects.

### SETUP_LOCAL.md
**Complete step-by-step guide.** Explains every step with context. Best for first-time setup.

### SETUP_READY.md
**Summary of what was created.** Quick overview of new files + what to do next.

### CONFIGURATION.md
**Technical reference.** What each placeholder is, where it appears, how to fix it.

### SETUP.md
**Original comprehensive guide.** Includes Azure provisioning (§1-2), deployment (§5-6), CI/CD (§8), optional features (§9).

### VALIDATION.md
**Setup verification checklist.** Comprehensive checklist to verify every part of setup is working. Also includes integration tests.

### README.md
**Product overview + API docs.** What is AutoApply, how does it work, API reference, FAQ.

### PROJECT_STATE.md
**Living codebase documentation.** Structure, schema, current state, known issues.

### api/local.settings.json.template
**Backend configuration template.** Copy this, fill in actual values, save as local.settings.json.

### tools/setup-local-dev.ps1
**Automated setup script.** Prompts for Azure info, creates config files, sets up venv, replaces placeholders.

---

## 🔄 Typical Setup Flow

```
1. Clone repo
   ↓
2. Read QUICKSTART.md (5 min)
   ↓
3. Provision Azure resources (see SETUP.md § 1-2)
   ↓
4. Run setup script: pwsh tools/setup-local-dev.ps1
   ↓
5. Run VALIDATION.md checklist to verify
   ↓
6. Done! Backend/frontend/extension all working locally
   ↓
7. (Optional) Deploy to Azure per SETUP.md § 5-6
```

---

## 📞 When You Get Stuck

| Problem | Where to Look |
|---------|---|
| Setup script fails | [SETUP_LOCAL.md](SETUP_LOCAL.md) § Troubleshooting |
| `func start` won't run | [VALIDATION.md](VALIDATION.md) § Backend Setup |
| Flutter app won't connect | [VALIDATION.md](VALIDATION.md) § Frontend Runtime Test |
| Extension can't autofill | [VALIDATION.md](VALIDATION.md) § Autofill Flow |
| Don't know what to configure | [CONFIGURATION.md](CONFIGURATION.md) |
| Don't understand the code | [PROJECT_STATE.md](PROJECT_STATE.md) |
| API endpoints not working | [README.md](README.md) § 4 (API Reference) |
| Need to deploy | [SETUP.md](SETUP.md) § 5-6 |

---

## 🆕 What's New (Setup Infrastructure)

These files were created to make setup easier:

- ✅ **SETUP_LOCAL.md** — Developer-friendly setup guide
- ✅ **SETUP_READY.md** — Summary of what's available
- ✅ **CONFIGURATION.md** — Technical reference
- ✅ **QUICKSTART.md** — Quick reference card
- ✅ **VALIDATION.md** — Verification checklist
- ✅ **API_REFERENCE.md** ← YOU ARE HERE (this index)
- ✅ **api/local.settings.json.template** — Config template
- ✅ **tools/setup-local-dev.ps1** — Automated setup script

---

## ✅ Setup Readiness Status

**Status: READY FOR LOCAL DEVELOPMENT**

- ✅ Documentation complete and comprehensive
- ✅ Automated setup script ready
- ✅ Configuration templates created
- ✅ Validation checklists provided
- ⏳ Requires: User to provision Azure resources + run setup script

---

## 🎓 Learning Path (Recommended Reading Order)

**For someone new to the project:**

1. [README.md](README.md) — Understand the product (10 min)
2. [QUICKSTART.md](QUICKSTART.md) — See what you need to do (5 min)
3. [SETUP_LOCAL.md](SETUP_LOCAL.md) — Follow step-by-step (20 min)
4. [VALIDATION.md](VALIDATION.md) — Verify setup works (15 min)
5. [PROJECT_STATE.md](PROJECT_STATE.md) — Understand the codebase (20 min)
6. [README.md § 4](README.md) — Learn the API (20 min)

**Total: ~90 minutes to understand + set up the entire system**

---

## 🔗 Quick Links

- 🚀 **Quick Start:** [QUICKSTART.md](QUICKSTART.md)
- 📖 **Setup Guide:** [SETUP_LOCAL.md](SETUP_LOCAL.md)
- ✅ **Validation:** [VALIDATION.md](VALIDATION.md)
- 🏗️ **Architecture:** [PROJECT_STATE.md](PROJECT_STATE.md)
- 📚 **API Docs:** [README.md § 4](README.md)
- ⚙️ **Configuration:** [CONFIGURATION.md](CONFIGURATION.md)
- 🔧 **Setup Script:** [tools/setup-local-dev.ps1](tools/setup-local-dev.ps1)
- 📋 **Original Guide:** [SETUP.md](SETUP.md)

---

**Last Updated:** 2026-05-20
**Status:** ✅ All setup infrastructure ready
