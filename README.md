# Git Detective
<p align="center">
  <img src="columbo-simplified.png" alt="Just One More Thing..." width="200">
</p>

A simple Python tool to analyze and visualize code hotspots in any git repository.

Based on concepts from **"Your Code as a Crime Scene"** by Adam Tornhill.

This project has (so far) been 100% vibe coded. It's not intended as a well structured project, and is just a prototype.

## Quick Start

```bash
# (RECOMMENDED) Run with patterns to not include in analysis and cache results (glob patterns)
python analyze_hotspots.py /path/to/your/repo --exclude "frontend/*" --exclude "docs/*" --exclude "tests/*" --cache

# Run analysis and store in cache
python analyze_hotspots.py /path/to/your/repo --cache

```

## What are Code Hotspots?

Hotspots are areas of your codebase that combine:
- **High change frequency** - files that are modified often
- **High complexity** - files with many lines of code

These are trouble hotspots - files that are complex AND keep changing. They're where bugs are most likely to occur and where refactoring efforts should focus.

## Adding Exclusions 
__**"I don't want to analyse that folder/file etc."**__

By default, the analyzer excludes files that aren't actual source code. If you'd like to add your own custom exclusion patterns, use the `--exclude` option one or more times when running the analyzer. 

For example, to skip all files in the `migrations/` folder and all Markdown files:

```bash
python analyze_hotspots.py /path/to/repo --exclude "migrations/*" --exclude "*.md"
```
These are in addition to the default built-in patterns.
You can also permanently modify exclusions by editing the `DEFAULT_EXCLUDES` list in `analyze_hotspots.py` if you want more fine-tuned control.

## How to Read the Visualization

- **Circle SIZE** = Lines of code (larger = more code)
- **Circle COLOR** = Change frequency (red = frequently changed)
- **HOTSPOT** = Large + Red = High priority for review/refactoring

## Credits

Inspired by:
- "Your Code as a Crime Scene" by Adam Tornhill
- Code Maat tool by Adam Tornhill
- CodeScene analysis platform

# Further Documentation
You can stop reading here if you just want to run the code. Below is just extra information on technical stuff.

## Metrics Collected

1. **Revisions** - Number of commits touching each file
2. **Lines of Code** - Current file size (complexity proxy)
3. **Authors** - Number of distinct contributors
4. **Code Churn** - Lines added/deleted over time

## Caching

The analyzer can cache results to avoid re-running analysis on unchanged repos:

```bash
# Enable caching
python analyze_hotspots.py /path/to/repo --cache
```

The cache automatically invalidates when:
- The repo's HEAD commit changes (new commits)
- The number of commits in the time period changes

Cache is stored in `.cache/` (self-contained) and can be managed with:
- `--list-cache` - See all cached analyses
- `--clear-cache` - Clear cache for current analysis
- `--clear-all-cache` - Clear everything


## Extended run command directory
```bash
# Analyze any git repository
python analyze_hotspots.py /path/to/your/repo

# Analyze with a date filter (only commits after a certain date)
python analyze_hotspots.py /path/to/your/repo --since 2024-01-01

# Don't auto-open browser
python analyze_hotspots.py /path/to/your/repo --no-open

# Specify output directory
python analyze_hotspots.py /path/to/your/repo --output ./my-analysis

# Add additional exclusion patterns
python analyze_hotspots.py /path/to/your/repo --exclude "*.test.js" --exclude "migrations/*"

# Show all default exclusion patterns
python analyze_hotspots.py . --show-excludes

# Disable default exclusions (analyze everything)
python analyze_hotspots.py /path/to/your/repo --no-default-excludes

# Use caching (instant results if repo hasn't changed)
python analyze_hotspots.py /path/to/your/repo --cache

# Force fresh analysis even if cache exists
python analyze_hotspots.py /path/to/your/repo --cache --no-cache

# List all cached analyses
python analyze_hotspots.py --list-cache

# Clear cache for a specific analysis
python analyze_hotspots.py /path/to/your/repo --clear-cache

# Clear all cached data
python analyze_hotspots.py --clear-all-cache
```