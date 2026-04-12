### itsdan.li
Welcome to my repository for my personal website
# WORK IN PROGRESS

## Test

## Publish a writing

Use the local publisher script to convert markdown into a site-compatible article and update `writings.html`.

```bash
python3 scripts/publish_writing.py path/to/post.md
```

The script will prompt for:

- `title`
- `date`
- `excerpt`
- whether to push immediately

Nothing is written until all prompts are completed. If you abort with `Ctrl+C`, `Ctrl+D`, or by entering `abort`, `quit`, or `exit`, no HTML file is created.

After the prompts, the script writes the article into `Essay_Folder/<slug>.html` and rebuilds the writings index from the published posts in `Essay_Folder/`.

If you choose to push immediately, the script runs `git add`, `git commit`, and `git push origin main` with the commit message:

```text
add writing <title>
```

It also supports simple front matter at the top of the markdown file:

```md
---
title: My Post
date: 2026-04-11
slug: my-post
excerpt: Short summary for the writings page
---
```
