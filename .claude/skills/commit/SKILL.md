---
name: commit
description: Stage changes and create a commit with an auto-generated message
---

# Commit

Stage changes and create a commit with an auto-generated conventional commit message.

## What to do

1. **Check git status**
   - Run `git status` to see modified, added, and deleted files
   - If no changes, report and exit

2. **Show diff summary**
   - Run `git diff --stat` for unstaged changes
   - Run `git diff --cached --stat` for staged changes
   - Show brief summary of what changed

3. **Analyze changes and generate commit message**
   - Read `git diff` output to understand the nature of changes
   - Determine the appropriate type prefix based on changes:
     - `feat:` - new feature or capability
     - `fix:` - bug fix
     - `docs:` - documentation only
     - `refactor:` - code restructuring without behavior change
     - `perf:` - performance improvement
     - `test:` - adding or updating tests
     - `chore:` - maintenance tasks, dependency updates
     - `style:` - formatting, whitespace
   - If changes span multiple files/areas, use the most significant scope
   - Generate a concise description (under 70 chars for the subject line)
   - Follow project patterns: `type: description` or `scope: description`

4. **Stage and commit**
   - Stage specific changed files (avoid `git add .` to prevent accidental commits)
   - Create commit with generated message

5. **Confirm result**
   - Show the commit hash and message
   - Run `git log -1 --stat` to display the commit

## Usage

```
/commit
```

Optionally provide a custom message:
```
/commit "fix: correct bcorr calculation for negative IR"
```

## Examples

Changes to `ops/list.py` adding parallelization:
```
list: parallelize refresh-bcorr with ProcessPoolExecutor (~16x speedup)
```

Changes to multiple CLAUDE.md files:
```
docs: split CLAUDE.md into directory-scoped files for context efficiency
```

Bug fix in parser:
```
fix: simsummary parser column shift for negative IR factors
```
