# GitHub Publishing Guide

## 1. Initialize the repository

```bash
git init
git add .
git commit -m "Initial public release"
```

## 2. Create a remote repository

Create a new empty GitHub repository, for example:

`civitai-post-tracker`

Then connect it:

```bash
git remote add origin https://github.com/YOUR_NAME/civitai-post-tracker.git
git branch -M main
git push -u origin main
```

## 3. Create a release

After pushing the code:

- open the GitHub repository
- go to **Releases**
- create a new tag, for example `v8.2.0`
- attach a clean zip archive if desired

## 4. Keep local secrets out of Git

Always verify:

```bash
git status
```

before committing.

If a local config or API key was accidentally added before, remove it from Git history before publishing.
