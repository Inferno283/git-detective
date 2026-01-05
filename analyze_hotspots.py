#!/usr/bin/env python3
"""
Code Hotspot Analyzer
=====================
A simple Python tool to analyze code hotspots in any git repository.

Based on concepts from "Your Code as a Crime Scene" by Adam Tornhill.

Hotspots are identified by combining:
- Change frequency (how often a file is modified)
- Code complexity (approximated by lines of code)

Usage:
    python analyze_hotspots.py /path/to/your/repo
    python analyze_hotspots.py /path/to/your/repo --since 2024-01-01
    python analyze_hotspots.py /path/to/your/repo --no-open

The script will generate an interactive D3.js visualization and open it in your browser.
"""

import subprocess
import os
import sys
import json
import argparse
import http.server
import socketserver
import webbrowser
import threading
import re
import fnmatch
import hashlib
from collections import defaultdict
from pathlib import Path
from datetime import datetime


# Cache directory for storing analysis results (inside the script's directory)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, '.cache')


# Default patterns to exclude from analysis
# These are files that are typically generated, dependencies, or not actual source code
DEFAULT_EXCLUSIONS = [
    # Lock files (generated, not source code)
    'yarn.lock',
    'package-lock.json',
    'pnpm-lock.yaml',
    'poetry.lock',
    'Pipfile.lock',
    'uv.lock',
    'go.sum',
    'Gemfile.lock',
    'composer.lock',
    'Cargo.lock',
    
    # Package/dependency directories
    'node_modules/*',
    'vendor/*',
    'venv/*',
    '.venv/*',
    'env/*',
    '.env/*',
    '__pycache__/*',
    '.pytest_cache/*',
    '.mypy_cache/*',
    '.tox/*',
    'site-packages/*',
    'dist-packages/*',
    
    # Build/output directories
    'dist/*',
    'build/*',
    'target/*',
    'out/*',
    '.next/*',
    '.nuxt/*',
    'coverage/*',
    '.coverage',
    'htmlcov/*',
    '*.egg-info/*',
    
    # IDE/editor files
    '.idea/*',
    '.vscode/*',
    '*.swp',
    '*.swo',
    '.DS_Store',
    'Thumbs.db',
    
    # Images and binary files
    '*.png',
    '*.jpg',
    '*.jpeg',
    '*.gif',
    '*.ico',
    '*.svg',
    '*.webp',
    '*.bmp',
    '*.tiff',
    '*.pdf',
    '*.zip',
    '*.tar',
    '*.gz',
    '*.rar',
    '*.7z',
    '*.woff',
    '*.woff2',
    '*.ttf',
    '*.eot',
    '*.mp3',
    '*.mp4',
    '*.wav',
    '*.avi',
    '*.mov',
    
    # Compiled/generated files
    '*.pyc',
    '*.pyo',
    '*.class',
    '*.o',
    '*.so',
    '*.dll',
    '*.exe',
    '*.jar',
    '*.war',
    '*.min.js',
    '*.min.css',
    '*.map',
    '*.bundle.js',
    '*.chunk.js',
    
    # Terraform/infrastructure state
    '*.tfstate',
    '*.tfstate.backup',
    '.terraform/*',
    '.terraform.lock.hcl',
    
    # Test fixtures and data (often large/generated)
    '*.log',
    'test/fixtures/*',
    'tests/fixtures/*',
    '__snapshots__/*',
    
    # Documentation/generated docs
    'docs/_build/*',
    'site/*',
    '.docusaurus/*',
    
    # Misc generated
    '*.generated.*',
    '*.auto.*',
    'package-lock.json',
]


def get_git_head_hash(repo_path):
    """Get the current HEAD commit hash for cache validation."""
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def get_git_commit_count(repo_path, since_date=None):
    """Get the number of commits (used for cache validation)."""
    try:
        args = ['git', '-C', repo_path, 'rev-list', '--count', 'HEAD']
        if since_date:
            args.extend(['--after', since_date])
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def generate_cache_key(repo_path, since_date, exclusions):
    """Generate a unique cache key based on analysis parameters."""
    # Normalize repo path
    repo_path = os.path.abspath(repo_path)
    
    # Create a string representation of the parameters
    params = {
        'repo': repo_path,
        'since': since_date or '',
        'exclusions': sorted(exclusions) if exclusions else []
    }
    params_str = json.dumps(params, sort_keys=True)
    
    # Create a hash of the parameters
    return hashlib.md5(params_str.encode()).hexdigest()


def get_cache_path(cache_key):
    """Get the path to the cache file for a given key."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f'{cache_key}.json')


def load_cache(repo_path, since_date, exclusions):
    """
    Load cached analysis results if valid.
    Returns (data, is_valid) tuple.
    """
    cache_key = generate_cache_key(repo_path, since_date, exclusions)
    cache_path = get_cache_path(cache_key)
    
    if not os.path.exists(cache_path):
        return None, False
    
    try:
        with open(cache_path, 'r') as f:
            cached = json.load(f)
        
        # Validate cache - check if repo HEAD has changed
        current_head = get_git_head_hash(repo_path)
        cached_head = cached.get('git_head')
        
        # Also check commit count for the time period
        current_count = get_git_commit_count(repo_path, since_date)
        cached_count = cached.get('commit_count')
        
        if current_head == cached_head and current_count == cached_count:
            return cached, True
        else:
            return cached, False  # Cache exists but is stale
            
    except (json.JSONDecodeError, KeyError, IOError):
        return None, False


def save_cache(repo_path, since_date, exclusions, data):
    """Save analysis results to cache."""
    cache_key = generate_cache_key(repo_path, since_date, exclusions)
    cache_path = get_cache_path(cache_key)
    
    # Add cache metadata
    data['git_head'] = get_git_head_hash(repo_path)
    data['commit_count'] = get_git_commit_count(repo_path, since_date)
    data['cached_at'] = datetime.now().isoformat()
    data['cache_key'] = cache_key
    
    try:
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)
        return cache_path
    except IOError as e:
        print(f"Warning: Could not save cache: {e}")
        return None


def clear_cache(repo_path=None, since_date=None, exclusions=None, clear_all=False):
    """Clear cache files."""
    if clear_all:
        # Clear all cache files
        if os.path.exists(CACHE_DIR):
            import shutil
            shutil.rmtree(CACHE_DIR)
            os.makedirs(CACHE_DIR)
        return True
    elif repo_path:
        # Clear cache for specific parameters
        cache_key = generate_cache_key(repo_path, since_date, exclusions)
        cache_path = get_cache_path(cache_key)
        if os.path.exists(cache_path):
            os.remove(cache_path)
            return True
    return False


def list_cached_analyses():
    """List all cached analyses."""
    if not os.path.exists(CACHE_DIR):
        return []
    
    cached = []
    for filename in os.listdir(CACHE_DIR):
        if filename.endswith('.json'):
            filepath = os.path.join(CACHE_DIR, filename)
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                cached.append({
                    'file': filename,
                    'repo': data.get('repository', 'Unknown'),
                    'cached_at': data.get('cached_at', 'Unknown'),
                    'since': data.get('since_date', 'All time'),
                    'files': len(data.get('hotspots', []))
                })
            except (json.JSONDecodeError, IOError):
                continue
    
    return cached


def should_exclude(filepath, exclusion_patterns):
    """
    Check if a file should be excluded based on exclusion patterns.
    Supports glob-style patterns.
    """
    filepath_lower = filepath.lower()
    
    for pattern in exclusion_patterns:
        pattern_lower = pattern.lower()
        
        # Check if pattern matches the full path or just the filename
        if fnmatch.fnmatch(filepath_lower, pattern_lower):
            return True
        if fnmatch.fnmatch(filepath_lower, '*/' + pattern_lower):
            return True
        # Also check if any path component matches
        if '/' in filepath:
            parts = filepath_lower.split('/')
            for i, part in enumerate(parts):
                # Check filename match
                if fnmatch.fnmatch(part, pattern_lower.rstrip('/*')):
                    return True
                # Check partial path match
                partial_path = '/'.join(parts[i:])
                if fnmatch.fnmatch(partial_path, pattern_lower):
                    return True
    
    return False


def run_git_command(repo_path, args):
    """Run a git command and return the output."""
    cmd = ['git', '-C', repo_path] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {' '.join(cmd)}")
        print(f"Error: {e.stderr}")
        return None


def get_revision_frequency(repo_path, since_date=None, exclusions=None):
    """
    Get the number of times each file has been modified.
    This is the core metric for identifying hotspots.
    """
    if exclusions is None:
        exclusions = DEFAULT_EXCLUSIONS
        
    args = ['log', '--name-only', '--pretty=format:']
    
    if since_date:
        args.append(f'--after={since_date}')
    
    output = run_git_command(repo_path, args)
    if output is None:
        return {}
    
    file_revisions = defaultdict(int)
    for line in output.split('\n'):
        line = line.strip()
        if line and not line.startswith('commit'):
            if not should_exclude(line, exclusions):
                file_revisions[line] += 1
    
    return dict(file_revisions)


def get_churn_data(repo_path, since_date=None, exclusions=None):
    """
    Get lines added/deleted per file (code churn).
    High churn indicates instability.
    """
    if exclusions is None:
        exclusions = DEFAULT_EXCLUSIONS
        
    args = ['log', '--numstat', '--pretty=format:']
    
    if since_date:
        args.append(f'--after={since_date}')
    
    output = run_git_command(repo_path, args)
    if output is None:
        return {}
    
    file_churn = defaultdict(lambda: {'added': 0, 'deleted': 0})
    
    for line in output.split('\n'):
        parts = line.split('\t')
        if len(parts) == 3:
            added, deleted, filename = parts
            if should_exclude(filename, exclusions):
                continue
            try:
                file_churn[filename]['added'] += int(added) if added != '-' else 0
                file_churn[filename]['deleted'] += int(deleted) if deleted != '-' else 0
            except ValueError:
                continue
    
    return dict(file_churn)


def get_author_count(repo_path, since_date=None, exclusions=None):
    """
    Get the number of distinct authors per file.
    More authors = more coordination complexity.
    """
    if exclusions is None:
        exclusions = DEFAULT_EXCLUSIONS
        
    args = ['log', '--name-only', '--pretty=format:%aN']
    
    if since_date:
        args.append(f'--after={since_date}')
    
    output = run_git_command(repo_path, args)
    if output is None:
        return {}
    
    file_authors = defaultdict(set)
    current_author = None
    
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        # If line contains typical file path characters, it's likely a file
        if '/' in line or '.' in line:
            if current_author and not should_exclude(line, exclusions):
                file_authors[line].add(current_author)
        else:
            current_author = line
    
    return {f: len(authors) for f, authors in file_authors.items()}


def get_commit_messages(repo_path, since_date=None, exclusions=None):
    """
    Get commit messages and hashes for each file.
    Returns a dict mapping filepath to list of {hash, message, author, date} dicts.
    """
    if exclusions is None:
        exclusions = DEFAULT_EXCLUSIONS
    
    # Get commits with files changed
    args = ['log', '--name-only', '--pretty=format:COMMIT:%h|%aN|%ad|%s', '--date=short']
    
    if since_date:
        args.append(f'--after={since_date}')
    
    output = run_git_command(repo_path, args)
    if output is None:
        return {}
    
    file_commits = defaultdict(list)
    current_commit = None
    
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        if line.startswith('COMMIT:'):
            # Parse commit info: hash|author|date|message
            parts = line[7:].split('|', 3)
            if len(parts) >= 4:
                current_commit = {
                    'hash': parts[0],
                    'author': parts[1],
                    'date': parts[2],
                    'message': parts[3]
                }
            else:
                current_commit = None
        elif current_commit and (('/' in line) or ('.' in line)):
            # This is a file path
            if not should_exclude(line, exclusions):
                # Add commit to this file's history
                file_commits[line].append(current_commit.copy())
    
    return dict(file_commits)


def count_lines_of_code(repo_path, exclusions=None):
    """
    Count lines of code for each file currently in the repository.
    This approximates complexity.
    """
    if exclusions is None:
        exclusions = DEFAULT_EXCLUSIONS
        
    file_loc = {}
    
    # Get list of tracked files
    output = run_git_command(repo_path, ['ls-files'])
    if output is None:
        return {}
    
    for filepath in output.split('\n'):
        filepath = filepath.strip()
        if not filepath:
            continue
        
        # Skip excluded files
        if should_exclude(filepath, exclusions):
            continue
        
        full_path = os.path.join(repo_path, filepath)
        
        # Skip binary files and non-existent files
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            continue
        
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = len(f.readlines())
                file_loc[filepath] = lines
        except Exception:
            continue
    
    return file_loc


def calculate_hotspots(revisions, loc, churn=None, authors=None):
    """
    Calculate hotspot scores for each file.
    
    Hotspot score = revisions * normalized_complexity
    
    Files with high revisions AND high complexity are the real hotspots.
    """
    hotspots = []
    
    # Get max values for normalization
    max_revisions = max(revisions.values()) if revisions else 1
    max_loc = max(loc.values()) if loc else 1
    
    # Only analyze files that exist in both datasets
    common_files = set(revisions.keys()) & set(loc.keys())
    
    for filepath in common_files:
        rev_count = revisions[filepath]
        lines = loc[filepath]
        
        # Skip very small files
        if lines < 10:
            continue
        
        # Normalize scores (0-1 scale)
        norm_revisions = rev_count / max_revisions
        norm_loc = lines / max_loc
        
        # Hotspot score: combination of frequency and size
        # Higher weight on revisions as that's the key indicator
        hotspot_score = (norm_revisions * 0.7) + (norm_loc * 0.3)
        
        entry = {
            'file': filepath,
            'revisions': rev_count,
            'lines': lines,
            'hotspot_score': round(hotspot_score, 4),
            'norm_revisions': round(norm_revisions, 4)
        }
        
        # Add optional data if available
        if churn and filepath in churn:
            entry['churn_added'] = churn[filepath]['added']
            entry['churn_deleted'] = churn[filepath]['deleted']
            entry['total_churn'] = churn[filepath]['added'] + churn[filepath]['deleted']
        
        if authors and filepath in authors:
            entry['authors'] = authors[filepath]
        
        hotspots.append(entry)
    
    # Sort by hotspot score descending
    hotspots.sort(key=lambda x: x['hotspot_score'], reverse=True)
    
    return hotspots


def build_hierarchy(hotspots):
    """
    Build a hierarchical structure for D3.js circle packing visualization.
    Groups files by directory structure.
    """
    root = {'name': 'root', 'children': {}}
    
    for entry in hotspots:
        path_parts = entry['file'].split('/')
        current = root
        
        # Navigate/create directory structure
        for i, part in enumerate(path_parts[:-1]):
            if part not in current['children']:
                current['children'][part] = {'name': part, 'children': {}}
            current = current['children'][part]
        
        # Add the file as a leaf node
        filename = path_parts[-1]
        current['children'][filename] = {
            'name': filename,
            'fullPath': entry['file'],
            'size': entry['lines'],
            'revisions': entry['revisions'],
            'hotspot_score': entry['hotspot_score'],
            'norm_revisions': entry['norm_revisions']
        }
        
        # Add optional fields
        if 'authors' in entry:
            current['children'][filename]['authors'] = entry['authors']
        if 'total_churn' in entry:
            current['children'][filename]['churn'] = entry['total_churn']
        if 'commits' in entry:
            current['children'][filename]['commits'] = entry['commits']
    
    # Convert children dicts to lists for D3
    def convert_to_list(node):
        if 'children' in node and isinstance(node['children'], dict):
            children_list = []
            for child_node in node['children'].values():
                children_list.append(convert_to_list(child_node))
            if children_list:
                node['children'] = children_list
            else:
                del node['children']
        return node
    
    return convert_to_list(root)


def generate_html(output_dir):
    """Generate the D3.js visualization HTML file."""
    
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code Hotspot Analysis</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Outfit:wght@300;500;700&display=swap');
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Outfit', sans-serif;
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            overflow-x: hidden;
        }
        
        .header {
            padding: 2rem 3rem;
            background: linear-gradient(180deg, rgba(15, 15, 26, 0.95) 0%, rgba(15, 15, 26, 0) 100%);
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 100;
        }
        
        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #f72585 0%, #7209b7 50%, #3a0ca3 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.02em;
        }
        
        .subtitle {
            font-size: 1rem;
            color: #888;
            margin-top: 0.5rem;
            font-weight: 300;
        }
        
        .container {
            display: flex;
            padding-top: 120px;
            padding-bottom: 130px; /* Space for date filter */
            min-height: 100vh;
        }
        
        .visualization {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1rem;
            margin-right: 380px;
            overflow: hidden;
        }
        
        #chart {
            background: radial-gradient(ellipse at center, rgba(58, 12, 163, 0.1) 0%, transparent 70%);
            border-radius: 20px;
            overflow: hidden;
        }
        
        #chart svg {
            display: block;
        }
        
        .sidebar {
            width: 380px;
            min-width: 280px;
            max-width: 800px;
            background: rgba(26, 26, 46, 0.8);
            backdrop-filter: blur(20px);
            border-left: 1px solid rgba(255, 255, 255, 0.05);
            padding: 2rem;
            overflow-y: auto;
            max-height: calc(100vh - 120px);
            position: fixed;
            right: 0;
            top: 120px;
            bottom: 0;
            transition: width 0s;
        }
        
        .sidebar-resizer {
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 6px;
            cursor: ew-resize;
            background: transparent;
            transition: background 0.2s ease;
        }
        
        .sidebar-resizer:hover,
        .sidebar-resizer.active {
            background: linear-gradient(180deg, #f72585 0%, #7209b7 100%);
        }
        
        .file-detail-view {
            display: none;
        }
        
        .file-detail-view.active {
            display: block;
        }
        
        .main-view {
            display: block;
        }
        
        .main-view.hidden {
            display: none;
        }
        
        .file-header {
            margin-bottom: 1.5rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .file-name {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1rem;
            color: #f72585;
            word-break: break-all;
            margin-bottom: 0.75rem;
        }
        
        .file-stats {
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            font-size: 0.8rem;
            color: #888;
        }
        
        .file-stats span {
            background: rgba(255, 255, 255, 0.05);
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
        }
        
        .back-button {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.85rem;
            color: #4cc9f0;
            cursor: pointer;
            margin-bottom: 1rem;
            padding: 0.5rem 0;
            transition: color 0.2s ease;
        }
        
        .back-button:hover {
            color: #f72585;
        }
        
        .commits-header {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 1rem;
        }
        
        .commit-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        
        .commit-item {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            padding: 1rem;
            transition: all 0.2s ease;
        }
        
        .commit-item:hover {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 255, 255, 0.1);
        }
        
        .commit-hash {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            color: #4cc9f0;
            margin-bottom: 0.5rem;
        }
        
        .commit-message {
            font-size: 0.9rem;
            color: #e0e0e0;
            line-height: 1.4;
            margin-bottom: 0.5rem;
        }
        
        .commit-meta {
            font-size: 0.75rem;
            color: #666;
            display: flex;
            gap: 1rem;
        }
        
        .no-commits {
            color: #666;
            font-size: 0.9rem;
            font-style: italic;
            padding: 1rem;
            text-align: center;
        }
        
        .legend {
            margin-bottom: 2rem;
        }
        
        .legend h3 {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 1rem;
        }
        
        .legend-gradient {
            height: 20px;
            background: linear-gradient(90deg, #2d6a4f, #40916c, #95d5b2, #ffd60a, #ff9500, #f72585, #b5179e);
            border-radius: 10px;
            margin-bottom: 0.5rem;
        }
        
        .legend-labels {
            display: flex;
            justify-content: space-between;
            font-size: 0.75rem;
            color: #888;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .stats {
            margin-bottom: 2rem;
        }
        
        .stat-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 1.25rem;
            margin-bottom: 1rem;
        }
        
        .stat-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 0.5rem;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #4cc9f0 0%, #4361ee 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .top-hotspots h3 {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
            margin-bottom: 1rem;
        }
        
        .hotspot-item {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 0.75rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .hotspot-item:hover {
            background: rgba(247, 37, 133, 0.1);
            border-color: rgba(247, 37, 133, 0.3);
            transform: translateX(4px);
        }
        
        .hotspot-name {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            color: #f0f0f0;
            margin-bottom: 0.5rem;
            word-break: break-all;
        }
        
        .hotspot-meta {
            display: flex;
            gap: 1rem;
            font-size: 0.75rem;
            color: #888;
        }
        
        .hotspot-meta span {
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }
        
        .tooltip {
            position: fixed;
            background: rgba(15, 15, 26, 0.95);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(247, 37, 133, 0.3);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            pointer-events: none;
            z-index: 1000;
            max-width: 350px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
        }
        
        .tooltip-title {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            color: #f72585;
            margin-bottom: 0.75rem;
            word-break: break-all;
        }
        
        .tooltip-row {
            display: flex;
            justify-content: space-between;
            font-size: 0.8rem;
            padding: 0.25rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        
        .tooltip-row:last-child {
            border-bottom: none;
        }
        
        .tooltip-label {
            color: #888;
        }
        
        .tooltip-value {
            font-family: 'JetBrains Mono', monospace;
            color: #4cc9f0;
        }
        
        circle {
            transition: all 0.2s ease;
        }
        
        circle:hover {
            filter: brightness(1.3);
        }
        
        .breadcrumb {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            color: #666;
            padding: 0.75rem 1rem;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 8px;
            margin-bottom: 1.5rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .breadcrumb:hover {
            color: #4cc9f0;
            background: rgba(67, 97, 238, 0.1);
        }
        
        .instructions {
            font-size: 0.8rem;
            color: #666;
            line-height: 1.6;
            padding: 1rem;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 8px;
            margin-top: 1.5rem;
        }
        
        .instructions strong {
            color: #888;
        }
        
        /* Date Range Slider */
        .date-filter-container {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 380px;
            background: rgba(15, 15, 26, 0.95);
            backdrop-filter: blur(20px);
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            padding: 1rem 2rem;
            z-index: 100;
        }
        
        .date-filter-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.75rem;
        }
        
        .date-filter-title {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #666;
        }
        
        .date-filter-range {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            color: #4cc9f0;
        }
        
        .date-filter-controls {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        
        .date-input-group {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .date-input-group label {
            font-size: 0.75rem;
            color: #666;
            text-transform: uppercase;
        }
        
        .date-input {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 0.5rem 0.75rem;
            color: #f0f0f0;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            width: 130px;
        }
        
        .date-input:focus {
            outline: none;
            border-color: #4cc9f0;
        }
        
        .slider-container {
            flex: 1;
            padding: 0 1rem;
        }
        
        .dual-slider {
            position: relative;
            height: 30px;
        }
        
        .slider-track {
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
        }
        
        .slider-range {
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            height: 6px;
            background: linear-gradient(90deg, #4361ee, #f72585);
            border-radius: 3px;
        }
        
        .range-slider {
            position: absolute;
            width: 100%;
            height: 6px;
            top: 50%;
            transform: translateY(-50%);
            -webkit-appearance: none;
            appearance: none;
            background: transparent;
            pointer-events: none;
        }
        
        .range-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 18px;
            height: 18px;
            background: #f0f0f0;
            border-radius: 50%;
            cursor: pointer;
            pointer-events: auto;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
            transition: transform 0.15s ease, background 0.15s ease;
        }
        
        .range-slider::-webkit-slider-thumb:hover {
            transform: scale(1.2);
            background: #4cc9f0;
        }
        
        .range-slider::-moz-range-thumb {
            width: 18px;
            height: 18px;
            background: #f0f0f0;
            border-radius: 50%;
            cursor: pointer;
            pointer-events: auto;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
            border: none;
        }
        
        .filter-stats {
            display: flex;
            gap: 1.5rem;
            margin-top: 0.5rem;
            font-size: 0.8rem;
            color: #888;
        }
        
        .filter-stats span {
            display: flex;
            align-items: center;
            gap: 0.25rem;
        }
        
        .filter-stats .value {
            color: #f0f0f0;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .reset-filter-btn {
            background: rgba(247, 37, 133, 0.2);
            border: 1px solid rgba(247, 37, 133, 0.3);
            color: #f72585;
            padding: 0.4rem 0.75rem;
            border-radius: 6px;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .reset-filter-btn:hover {
            background: rgba(247, 37, 133, 0.3);
            border-color: #f72585;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Code Hotspot Analysis</h1>
        <p class="subtitle">Visualizing areas of high change frequency & complexity</p>
    </div>
    
    <div class="container">
        <div class="visualization">
            <div id="chart"></div>
        </div>
        
        <div class="sidebar" id="sidebar">
            <div class="sidebar-resizer" id="sidebar-resizer"></div>
            
            <!-- Main View (default) -->
            <div class="main-view" id="main-view">
                <div class="breadcrumb" id="breadcrumb" onclick="zoomToRoot()">
                    📁 root
                </div>
                
                <div class="legend">
                    <h3>Hotspot Intensity</h3>
                    <div class="legend-gradient"></div>
                    <div class="legend-labels">
                        <span>Cool (Low)</span>
                        <span>Hot (High)</span>
                    </div>
                </div>
                
                <div class="stats">
                    <div class="stat-card">
                        <div class="stat-label">Total Files Analyzed</div>
                        <div class="stat-value" id="total-files">-</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Revisions</div>
                        <div class="stat-value" id="total-revisions">-</div>
                    </div>
                </div>
                
                <div class="top-hotspots">
                    <h3>🔥 Top Hotspots</h3>
                    <div id="hotspot-list"></div>
                </div>
                
                <div class="instructions">
                    <strong>How to read this:</strong><br>
                    • Circle <strong>size</strong> = lines of code<br>
                    • Circle <strong>color</strong> = change frequency (red = high)<br>
                    • <strong>Click</strong> on file to see commit history<br>
                    • <strong>Click</strong> on directory to zoom in<br>
                    • <strong>Scroll wheel</strong> to zoom in/out freely<br>
                    • <strong>Drag</strong> to pan around<br>
                    • <strong>Double-click</strong> to reset view
                </div>
            </div>
            
            <!-- File Detail View (shown when clicking a file) -->
            <div class="file-detail-view" id="file-detail-view">
                <div class="back-button" id="back-to-main">
                    ← Back to overview
                </div>
                
                <div class="file-header">
                    <div class="file-name" id="detail-file-name">-</div>
                    <div class="file-stats" id="detail-file-stats"></div>
                </div>
                
                <div class="commits-header">💬 Commit History</div>
                <div class="commit-list" id="commit-list"></div>
            </div>
        </div>
    </div>
    
    <div class="tooltip" id="tooltip" style="display: none;"></div>
    
    <!-- Date Range Filter -->
    <div class="date-filter-container" id="date-filter">
        <div class="date-filter-header">
            <span class="date-filter-title">📅 Filter by Date Range</span>
            <span class="date-filter-range" id="date-range-display">Loading...</span>
            <button class="reset-filter-btn" id="reset-filter-btn">Reset</button>
        </div>
        <div class="date-filter-controls">
            <div class="date-input-group">
                <label>From</label>
                <input type="date" class="date-input" id="start-date-input">
            </div>
            <div class="slider-container">
                <div class="dual-slider">
                    <div class="slider-track"></div>
                    <div class="slider-range" id="slider-range"></div>
                    <input type="range" class="range-slider" id="start-slider" min="0" max="100" value="0">
                    <input type="range" class="range-slider" id="end-slider" min="0" max="100" value="100">
                </div>
            </div>
            <div class="date-input-group">
                <label>To</label>
                <input type="date" class="date-input" id="end-date-input">
            </div>
        </div>
        <div class="filter-stats">
            <span>Files in range: <span class="value" id="filtered-files">-</span></span>
            <span>Commits in range: <span class="value" id="filtered-commits">-</span></span>
            <span>Authors in range: <span class="value" id="filtered-authors">-</span></span>
        </div>
    </div>

    <script>
        // Load the data
        d3.json('hotspot_data.json').then(function(rawData) {
            const data = rawData.hierarchy;
            const hotspots = rawData.hotspots;
            const originalHotspots = JSON.parse(JSON.stringify(hotspots)); // Deep copy for reset
            
            // Update stats
            document.getElementById('total-files').textContent = hotspots.length.toLocaleString();
            document.getElementById('total-revisions').textContent = 
                hotspots.reduce((sum, h) => sum + h.revisions, 0).toLocaleString();
            
            // Populate top hotspots list
            const hotspotList = document.getElementById('hotspot-list');
            hotspots.slice(0, 10).forEach((h, i) => {
                const item = document.createElement('div');
                item.className = 'hotspot-item';
                item.innerHTML = `
                    <div class="hotspot-name">${h.file}</div>
                    <div class="hotspot-meta">
                        <span>📝 ${h.revisions} revisions</span>
                        <span>📄 ${h.lines} lines</span>
                    </div>
                `;
                item.onclick = () => highlightFile(h.file);
                hotspotList.appendChild(item);
            });
            
            // Set up the visualization - fill available space
            const sidebarWidth = 380;
            const headerHeight = 120;
            const bottomBarHeight = 130;
            const padding = 40;
            
            const width = window.innerWidth - sidebarWidth - padding;
            const height = window.innerHeight - headerHeight - bottomBarHeight - padding;
            
            // Color scale based on revision intensity
            const colorScale = d3.scaleSequential()
                .domain([0, 1])
                .interpolator(d3.interpolateRgbBasis([
                    '#2d6a4f', '#40916c', '#74c69d', 
                    '#ffd60a', '#ff9500', 
                    '#f72585', '#b5179e'
                ]));
            
            // Create the pack layout
            const pack = d3.pack()
                .size([width - 4, height - 4])
                .padding(3);
            
            // Create hierarchy
            const root = d3.hierarchy(data)
                .sum(d => d.size || 0)
                .sort((a, b) => (b.data.norm_revisions || 0) - (a.data.norm_revisions || 0));
            
            pack(root);
            
            // Create SVG with zoom behavior
            const svg = d3.select('#chart')
                .append('svg')
                .attr('width', width)
                .attr('height', height)
                .attr('viewBox', [-width / 2, -height / 2, width, height])
                .style('cursor', 'grab');
            
            // Create main group that will be transformed
            const g = svg.append('g');
            
            // Track state
            let focus = root;
            let view;
            let currentTransform = d3.zoomIdentity;
            
            // Set up D3 zoom behavior for scroll wheel zoom and panning
            const zoomBehavior = d3.zoom()
                .scaleExtent([0.5, 10])  // Min and max zoom levels
                .on('start', function(event) {
                    svg.style('cursor', 'grabbing');
                })
                .on('zoom', function(event) {
                    currentTransform = event.transform;
                    g.attr('transform', event.transform);
                })
                .on('end', function(event) {
                    svg.style('cursor', 'grab');
                });
            
            // Apply zoom behavior to SVG
            svg.call(zoomBehavior);
            
            // Double-click to reset zoom
            svg.on('dblclick.zoom', null);  // Disable default double-click zoom
            svg.on('dblclick', function(event) {
                event.preventDefault();
                // Reset to initial view
                svg.transition()
                    .duration(750)
                    .call(zoomBehavior.transform, d3.zoomIdentity);
                focus = root;
                zoomToCircle([root.x, root.y, root.r * 2]);
                updateBreadcrumb(root);
            });
            
            // Create circles
            const node = g.append('g')
                .selectAll('circle')
                .data(root.descendants().slice(1))
                .join('circle')
                .attr('fill', d => {
                    if (d.children) {
                        // Directory - darker shade
                        return 'rgba(30, 30, 50, 0.8)';
                    }
                    // File - color based on hotspot intensity
                    const intensity = d.data.norm_revisions || 0;
                    return colorScale(intensity);
                })
                .attr('stroke', d => d.children ? 'rgba(255,255,255,0.1)' : 'none')
                .attr('stroke-width', 1)
                .style('opacity', d => d.children ? 0.7 : 0.85)
                .on('mouseover', function(event, d) {
                    d3.select(this).style('opacity', 1);
                    showTooltip(event, d);
                })
                .on('mousemove', function(event) {
                    moveTooltip(event);
                })
                .on('mouseout', function(d) {
                    d3.select(this).style('opacity', d => d.children ? 0.7 : 0.85);
                    hideTooltip();
                })
                .on('click', (event, d) => {
                    event.stopPropagation();
                    if (d.children) {
                        // Zoom into directory
                        focus = d;
                        zoomToCircle([d.x, d.y, d.r * 2]);
                        updateBreadcrumb(d);
                        
                        // Also adjust the D3 zoom transform to center on this node
                        const scale = Math.min(width, height) / (d.r * 4);
                        const x = -d.x * scale + width / 2;
                        const y = -d.y * scale + height / 2;
                        
                        svg.transition()
                            .duration(750)
                            .call(zoomBehavior.transform, 
                                  d3.zoomIdentity.translate(x - width/2, y - height/2).scale(scale));
                    } else {
                        // File clicked - show commit history
                        showFileDetail(d);
                    }
                });
            
            // Labels for directories
            const label = g.append('g')
                .style('font-family', 'JetBrains Mono, monospace')
                .attr('pointer-events', 'none')
                .attr('text-anchor', 'middle')
                .selectAll('text')
                .data(root.descendants())
                .join('text')
                .style('fill', '#fff')
                .style('fill-opacity', d => d.parent === root ? 1 : 0)
                .style('display', d => d.parent === root ? 'inline' : 'none')
                .style('font-size', d => d.children ? '11px' : '9px')
                .text(d => d.data.name.length > 15 ? d.data.name.slice(0, 12) + '...' : d.data.name);
            
            // Initial view - scale to fill horizontal space better
            const initialDiameter = root.r * 2;
            const horizontalScale = width / initialDiameter;
            const verticalScale = height / initialDiameter;
            const initialScale = Math.max(horizontalScale, verticalScale) * 0.95; // Use larger scale, with small margin
            
            zoomToCircle([root.x, root.y, root.r * 2]);
            
            // Apply initial scale transform if visualization is wider than tall
            if (width > height) {
                const scaleRatio = width / height;
                svg.call(zoomBehavior.transform, d3.zoomIdentity.scale(scaleRatio * 0.85));
            }
            
            function zoomToCircle(v) {
                const k = Math.min(width, height) / v[2];
                view = v;
                
                label.attr('transform', d => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`);
                node.attr('transform', d => `translate(${(d.x - v[0]) * k},${(d.y - v[1]) * k})`);
                node.attr('r', d => d.r * k);
            }
            
            // Click on SVG background to zoom out and show main view
            svg.on('click', (event) => {
                // Always return to main view when clicking background
                showMainView();
                
                if (focus !== root && focus.parent) {
                    focus = focus.parent;
                    zoomToCircle([focus.x, focus.y, focus.r * 2]);
                    updateBreadcrumb(focus);
                    
                    // Adjust D3 zoom transform
                    const scale = focus === root ? 1 : Math.min(width, height) / (focus.r * 4);
                    const x = -focus.x * scale + width / 2;
                    const y = -focus.y * scale + height / 2;
                    
                    svg.transition()
                        .duration(750)
                        .call(zoomBehavior.transform, 
                              focus === root ? d3.zoomIdentity : 
                              d3.zoomIdentity.translate(x - width/2, y - height/2).scale(scale));
                }
            });
            
            // Make zoom to root globally available
            window.zoomToRoot = function() {
                focus = root;
                zoomToCircle([root.x, root.y, root.r * 2]);
                updateBreadcrumb(root);
                svg.transition()
                    .duration(750)
                    .call(zoomBehavior.transform, d3.zoomIdentity);
            };
            
            function updateBreadcrumb(d) {
                const path = [];
                let current = d;
                while (current) {
                    path.unshift(current.data.name);
                    current = current.parent;
                }
                document.getElementById('breadcrumb').textContent = '📁 ' + path.join(' / ');
            }
            
            function showTooltip(event, d) {
                const tooltip = document.getElementById('tooltip');
                let content = `<div class="tooltip-title">${d.data.fullPath || d.data.name}</div>`;
                
                if (!d.children) {
                    content += `
                        <div class="tooltip-row">
                            <span class="tooltip-label">Lines of Code</span>
                            <span class="tooltip-value">${(d.data.size || 0).toLocaleString()}</span>
                        </div>
                        <div class="tooltip-row">
                            <span class="tooltip-label">Revisions</span>
                            <span class="tooltip-value">${d.data.revisions || 0}</span>
                        </div>
                        <div class="tooltip-row">
                            <span class="tooltip-label">Hotspot Score</span>
                            <span class="tooltip-value">${((d.data.hotspot_score || 0) * 100).toFixed(1)}%</span>
                        </div>
                    `;
                    if (d.data.authors) {
                        content += `
                            <div class="tooltip-row">
                                <span class="tooltip-label">Authors</span>
                                <span class="tooltip-value">${d.data.authors}</span>
                            </div>
                        `;
                    }
                    if (d.data.churn) {
                        content += `
                            <div class="tooltip-row">
                                <span class="tooltip-label">Total Churn</span>
                                <span class="tooltip-value">${d.data.churn.toLocaleString()} lines</span>
                            </div>
                        `;
                    }
                } else {
                    const descendants = d.descendants().filter(n => !n.children);
                    content += `
                        <div class="tooltip-row">
                            <span class="tooltip-label">Files</span>
                            <span class="tooltip-value">${descendants.length}</span>
                        </div>
                        <div class="tooltip-row">
                            <span class="tooltip-label">Total Lines</span>
                            <span class="tooltip-value">${d.value.toLocaleString()}</span>
                        </div>
                    `;
                }
                
                tooltip.innerHTML = content;
                tooltip.style.display = 'block';
                moveTooltip(event);
            }
            
            function moveTooltip(event) {
                const tooltip = document.getElementById('tooltip');
                let x = event.clientX + 15;
                let y = event.clientY + 15;
                
                // Keep tooltip in viewport
                const rect = tooltip.getBoundingClientRect();
                if (x + rect.width > window.innerWidth - 400) {
                    x = event.clientX - rect.width - 15;
                }
                if (y + rect.height > window.innerHeight) {
                    y = event.clientY - rect.height - 15;
                }
                
                tooltip.style.left = x + 'px';
                tooltip.style.top = y + 'px';
            }
            
            function hideTooltip() {
                document.getElementById('tooltip').style.display = 'none';
            }
            
            // Highlight specific file from sidebar
            window.highlightFile = function(filepath) {
                const targetNode = root.descendants().find(d => d.data.fullPath === filepath);
                if (targetNode) {
                    // Zoom to the file's location
                    const parent = targetNode.parent || root;
                    focus = parent;
                    zoomToCircle([parent.x, parent.y, parent.r * 2]);
                    updateBreadcrumb(parent);
                    
                    // Adjust D3 zoom transform to center on the file
                    const scale = Math.min(width, height) / (parent.r * 4);
                    const x = -parent.x * scale + width / 2;
                    const y = -parent.y * scale + height / 2;
                    
                    svg.transition()
                        .duration(750)
                        .call(zoomBehavior.transform, 
                              d3.zoomIdentity.translate(x - width/2, y - height/2).scale(scale));
                    
                    // Flash the circle
                    node.filter(d => d.data.fullPath === filepath)
                        .transition()
                        .duration(200)
                        .attr('stroke', '#fff')
                        .attr('stroke-width', 3)
                        .transition()
                        .duration(1000)
                        .attr('stroke', 'none')
                        .attr('stroke-width', 0);
                    
                    // Show file detail view
                    showFileDetail(targetNode);
                }
            };
            
            // Show file detail view with commit history
            function showFileDetail(d) {
                const mainView = document.getElementById('main-view');
                const detailView = document.getElementById('file-detail-view');
                const fileName = document.getElementById('detail-file-name');
                const fileStats = document.getElementById('detail-file-stats');
                const commitList = document.getElementById('commit-list');
                
                // Update file name
                fileName.textContent = d.data.fullPath || d.data.name;
                
                // Update file stats
                fileStats.innerHTML = `
                    <span>📝 ${d.data.revisions || 0} revisions</span>
                    <span>📄 ${(d.data.size || 0).toLocaleString()} lines</span>
                    ${d.data.authors ? `<span>👥 ${d.data.authors} authors</span>` : ''}
                    ${d.data.churn ? `<span>📊 ${d.data.churn.toLocaleString()} churn</span>` : ''}
                `;
                
                // Update commit list
                const commits = d.data.commits || [];
                if (commits.length > 0) {
                    commitList.innerHTML = commits.map(c => `
                        <div class="commit-item">
                            <div class="commit-hash">${c.hash}</div>
                            <div class="commit-message">${escapeHtml(c.message)}</div>
                            <div class="commit-meta">
                                <span>👤 ${escapeHtml(c.author)}</span>
                                <span>📅 ${c.date}</span>
                            </div>
                        </div>
                    `).join('');
                } else {
                    commitList.innerHTML = '<div class="no-commits">No commit history available</div>';
                }
                
                // Switch views
                mainView.classList.add('hidden');
                detailView.classList.add('active');
                
                // Highlight the selected circle
                node.attr('stroke', n => n === d ? '#f72585' : (n.children ? 'rgba(255,255,255,0.1)' : 'none'))
                    .attr('stroke-width', n => n === d ? 3 : 1);
            }
            
            // Show main view
            function showMainView() {
                const mainView = document.getElementById('main-view');
                const detailView = document.getElementById('file-detail-view');
                
                mainView.classList.remove('hidden');
                detailView.classList.remove('active');
                
                // Remove highlighting
                node.attr('stroke', d => d.children ? 'rgba(255,255,255,0.1)' : 'none')
                    .attr('stroke-width', 1);
            }
            
            // Escape HTML for safe display
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }
            
            // Make showMainView globally available
            window.showMainView = showMainView;
            
            // Back button handler
            document.getElementById('back-to-main').addEventListener('click', showMainView);
            
            // Sidebar resizer
            const sidebar = document.getElementById('sidebar');
            const resizer = document.getElementById('sidebar-resizer');
            let isResizing = false;
            
            resizer.addEventListener('mousedown', (e) => {
                isResizing = true;
                resizer.classList.add('active');
                document.body.style.cursor = 'ew-resize';
                document.body.style.userSelect = 'none';
            });
            
            document.addEventListener('mousemove', (e) => {
                if (!isResizing) return;
                
                const newWidth = window.innerWidth - e.clientX;
                if (newWidth >= 280 && newWidth <= 800) {
                    sidebar.style.width = newWidth + 'px';
                }
            });
            
            document.addEventListener('mouseup', () => {
                if (isResizing) {
                    isResizing = false;
                    resizer.classList.remove('active');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                }
            });
            
            // ===== DATE RANGE FILTERING =====
            
            // Collect all commit dates from the data
            const allDates = [];
            hotspots.forEach(h => {
                if (h.commits) {
                    h.commits.forEach(c => {
                        if (c.date) {
                            allDates.push(new Date(c.date));
                        }
                    });
                }
            });
            
            if (allDates.length === 0) {
                // No commit dates available, hide the filter
                document.getElementById('date-filter').style.display = 'none';
            } else {
                // Find min and max dates
                const minDate = new Date(Math.min(...allDates));
                const maxDate = new Date(Math.max(...allDates));
                const dateRange = maxDate - minDate;
                
                // Store original data for each node
                root.descendants().forEach(d => {
                    if (d.data.commits) {
                        d.data.originalCommits = [...d.data.commits];
                        d.data.originalRevisions = d.data.revisions;
                        d.data.originalNormRevisions = d.data.norm_revisions;
                    }
                });
                
                // Initialize filter state
                let currentStartDate = minDate;
                let currentEndDate = maxDate;
                
                // DOM elements
                const startSlider = document.getElementById('start-slider');
                const endSlider = document.getElementById('end-slider');
                const startDateInput = document.getElementById('start-date-input');
                const endDateInput = document.getElementById('end-date-input');
                const sliderRange = document.getElementById('slider-range');
                const dateRangeDisplay = document.getElementById('date-range-display');
                const resetBtn = document.getElementById('reset-filter-btn');
                const filteredFilesEl = document.getElementById('filtered-files');
                const filteredCommitsEl = document.getElementById('filtered-commits');
                const filteredAuthorsEl = document.getElementById('filtered-authors');
                const dateFilterContainer = document.getElementById('date-filter');
                
                // Update sidebar width when filter is present
                dateFilterContainer.style.right = sidebar.style.width || '380px';
                
                // Format date for display
                function formatDate(date) {
                    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
                }
                
                // Format date for input
                function formatDateForInput(date) {
                    return date.toISOString().split('T')[0];
                }
                
                // Convert slider value (0-100) to date
                function sliderToDate(value) {
                    const ms = minDate.getTime() + (value / 100) * dateRange;
                    return new Date(ms);
                }
                
                // Convert date to slider value (0-100)
                function dateToSlider(date) {
                    return ((date.getTime() - minDate.getTime()) / dateRange) * 100;
                }
                
                // Update the visual slider range
                function updateSliderRange() {
                    const startPercent = parseFloat(startSlider.value);
                    const endPercent = parseFloat(endSlider.value);
                    sliderRange.style.left = startPercent + '%';
                    sliderRange.style.width = (endPercent - startPercent) + '%';
                }
                
                // Initialize inputs
                startDateInput.min = formatDateForInput(minDate);
                startDateInput.max = formatDateForInput(maxDate);
                startDateInput.value = formatDateForInput(minDate);
                endDateInput.min = formatDateForInput(minDate);
                endDateInput.max = formatDateForInput(maxDate);
                endDateInput.value = formatDateForInput(maxDate);
                
                // Update display
                function updateDateDisplay() {
                    dateRangeDisplay.textContent = `${formatDate(currentStartDate)} → ${formatDate(currentEndDate)}`;
                }
                updateDateDisplay();
                updateSliderRange();
                
                // Filter and recalculate hotspots
                function applyDateFilter() {
                    let totalFilteredCommits = 0;
                    let allAuthors = new Set();
                    let filesWithCommits = 0;
                    
                    // Find new max revisions for normalization
                    let maxFilteredRevisions = 0;
                    
                    // First pass: count filtered commits per file
                    root.descendants().forEach(d => {
                        if (d.data.originalCommits) {
                            const filteredCommits = d.data.originalCommits.filter(c => {
                                const commitDate = new Date(c.date);
                                return commitDate >= currentStartDate && commitDate <= currentEndDate;
                            });
                            d.data.filteredRevisions = filteredCommits.length;
                            maxFilteredRevisions = Math.max(maxFilteredRevisions, filteredCommits.length);
                        }
                    });
                    
                    if (maxFilteredRevisions === 0) maxFilteredRevisions = 1;
                    
                    // Second pass: update node data
                    root.descendants().forEach(d => {
                        if (d.data.originalCommits) {
                            const filteredCommits = d.data.originalCommits.filter(c => {
                                const commitDate = new Date(c.date);
                                return commitDate >= currentStartDate && commitDate <= currentEndDate;
                            });
                            
                            d.data.commits = filteredCommits;
                            d.data.revisions = filteredCommits.length;
                            d.data.norm_revisions = filteredCommits.length / maxFilteredRevisions;
                            
                            totalFilteredCommits += filteredCommits.length;
                            filteredCommits.forEach(c => allAuthors.add(c.author));
                            if (filteredCommits.length > 0) filesWithCommits++;
                        }
                    });
                    
                    // Update stats
                    filteredFilesEl.textContent = filesWithCommits.toLocaleString();
                    filteredCommitsEl.textContent = totalFilteredCommits.toLocaleString();
                    filteredAuthorsEl.textContent = allAuthors.size.toLocaleString();
                    
                    // Update sidebar stats
                    document.getElementById('total-revisions').textContent = totalFilteredCommits.toLocaleString();
                    
                    // Update circle colors
                    node.transition()
                        .duration(300)
                        .attr('fill', d => {
                            if (d.children) {
                                return 'rgba(30, 30, 50, 0.8)';
                            }
                            const intensity = d.data.norm_revisions || 0;
                            return colorScale(intensity);
                        });
                    
                    // Update top hotspots list
                    updateTopHotspotsList();
                }
                
                // Update top hotspots list in sidebar
                function updateTopHotspotsList() {
                    const fileNodes = root.descendants()
                        .filter(d => !d.children && d.data.originalCommits)
                        .map(d => ({
                            file: d.data.fullPath || d.data.name,
                            revisions: d.data.revisions,
                            lines: d.data.size,
                            node: d
                        }))
                        .sort((a, b) => b.revisions - a.revisions)
                        .slice(0, 10);
                    
                    const hotspotList = document.getElementById('hotspot-list');
                    hotspotList.innerHTML = '';
                    
                    fileNodes.forEach(h => {
                        const item = document.createElement('div');
                        item.className = 'hotspot-item';
                        item.innerHTML = `
                            <div class="hotspot-name">${h.file}</div>
                            <div class="hotspot-meta">
                                <span>📝 ${h.revisions} revisions</span>
                                <span>📄 ${h.lines} lines</span>
                            </div>
                        `;
                        item.onclick = () => {
                            highlightFile(h.file);
                        };
                        hotspotList.appendChild(item);
                    });
                }
                
                // Slider event handlers
                let sliderTimeout;
                
                startSlider.addEventListener('input', () => {
                    let startVal = parseFloat(startSlider.value);
                    let endVal = parseFloat(endSlider.value);
                    
                    // Prevent crossing
                    if (startVal > endVal - 1) {
                        startVal = endVal - 1;
                        startSlider.value = startVal;
                    }
                    
                    currentStartDate = sliderToDate(startVal);
                    startDateInput.value = formatDateForInput(currentStartDate);
                    updateSliderRange();
                    updateDateDisplay();
                    
                    // Debounce the filter update
                    clearTimeout(sliderTimeout);
                    sliderTimeout = setTimeout(applyDateFilter, 150);
                });
                
                endSlider.addEventListener('input', () => {
                    let startVal = parseFloat(startSlider.value);
                    let endVal = parseFloat(endSlider.value);
                    
                    // Prevent crossing
                    if (endVal < startVal + 1) {
                        endVal = startVal + 1;
                        endSlider.value = endVal;
                    }
                    
                    currentEndDate = sliderToDate(endVal);
                    endDateInput.value = formatDateForInput(currentEndDate);
                    updateSliderRange();
                    updateDateDisplay();
                    
                    // Debounce the filter update
                    clearTimeout(sliderTimeout);
                    sliderTimeout = setTimeout(applyDateFilter, 150);
                });
                
                // Date input handlers
                startDateInput.addEventListener('change', () => {
                    const newDate = new Date(startDateInput.value);
                    if (newDate >= minDate && newDate <= currentEndDate) {
                        currentStartDate = newDate;
                        startSlider.value = dateToSlider(newDate);
                        updateSliderRange();
                        updateDateDisplay();
                        applyDateFilter();
                    }
                });
                
                endDateInput.addEventListener('change', () => {
                    const newDate = new Date(endDateInput.value);
                    if (newDate <= maxDate && newDate >= currentStartDate) {
                        currentEndDate = newDate;
                        endSlider.value = dateToSlider(newDate);
                        updateSliderRange();
                        updateDateDisplay();
                        applyDateFilter();
                    }
                });
                
                // Reset button
                resetBtn.addEventListener('click', () => {
                    currentStartDate = minDate;
                    currentEndDate = maxDate;
                    startSlider.value = 0;
                    endSlider.value = 100;
                    startDateInput.value = formatDateForInput(minDate);
                    endDateInput.value = formatDateForInput(maxDate);
                    updateSliderRange();
                    updateDateDisplay();
                    applyDateFilter();
                });
                
                // Initial filter stats
                applyDateFilter();
                
                // Update filter container width when sidebar resizes
                const originalResizeHandler = document.onmousemove;
                document.addEventListener('mousemove', (e) => {
                    if (isResizing) {
                        const newWidth = window.innerWidth - e.clientX;
                        if (newWidth >= 280 && newWidth <= 800) {
                            dateFilterContainer.style.right = newWidth + 'px';
                        }
                    }
                });
            }
            
        }).catch(function(error) {
            console.error('Error loading data:', error);
            document.getElementById('chart').innerHTML = 
                '<p style="color: #f72585; padding: 2rem;">Error loading data. Make sure hotspot_data.json exists.</p>';
        });
    </script>
</body>
</html>'''
    
    html_path = os.path.join(output_dir, 'index.html')
    with open(html_path, 'w') as f:
        f.write(html_content)
    
    return html_path


def start_server(directory, port=8080):
    """Start a simple HTTP server to serve the visualization."""
    os.chdir(directory)
    
    handler = http.server.SimpleHTTPRequestHandler
    
    # Suppress server logs
    class QuietHandler(handler):
        def log_message(self, format, *args):
            pass
    
    # Try ports until we find an available one
    for p in range(port, port + 100):
        try:
            with socketserver.TCPServer(("", p), QuietHandler) as httpd:
                print(f"\n🌐 Visualization server running at: http://localhost:{p}")
                print("   Press Ctrl+C to stop the server\n")
                httpd.serve_forever()
                break
        except OSError:
            continue


def main():
    parser = argparse.ArgumentParser(
        description='Analyze code hotspots in a git repository',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
    %(prog)s /path/to/repo
    %(prog)s /path/to/repo --since 2024-01-01
    %(prog)s /path/to/repo --output ./results
    %(prog)s /path/to/repo --no-open
    %(prog)s /path/to/repo --exclude "*.test.js" --exclude "migrations/*"
    %(prog)s /path/to/repo --no-default-excludes
    %(prog)s /path/to/repo --show-excludes
    
Cache options:
    %(prog)s /path/to/repo --cache              # Use cached results if available
    %(prog)s /path/to/repo --no-cache           # Force fresh analysis (default)
    %(prog)s /path/to/repo --clear-cache        # Clear cache for this analysis
    %(prog)s --list-cache                       # List all cached analyses
    %(prog)s --clear-all-cache                  # Clear all cached data
        '''
    )
    
    parser.add_argument('repo_path', nargs='?', help='Path to the git repository to analyze')
    parser.add_argument('--since', help='Only analyze commits after this date (YYYY-MM-DD)')
    parser.add_argument('--output', '-o', help='Output directory for results (default: ./hotspot_output)')
    parser.add_argument('--port', '-p', type=int, default=8080, help='Port for the local server (default: 8080)')
    parser.add_argument('--no-open', action='store_true', help="Don't automatically open the browser")
    parser.add_argument('--no-serve', action='store_true', help="Don't start a server, just generate the files")
    parser.add_argument('--exclude', '-e', action='append', default=[], 
                        help='Additional patterns to exclude (can be used multiple times)')
    parser.add_argument('--no-default-excludes', action='store_true',
                        help='Disable default exclusions (lock files, node_modules, images, etc.)')
    parser.add_argument('--show-excludes', action='store_true',
                        help='Show the list of default exclusion patterns and exit')
    
    # Cache options
    parser.add_argument('--cache', action='store_true',
                        help='Use cached results if available (checks if repo has changed)')
    parser.add_argument('--no-cache', action='store_true',
                        help='Force fresh analysis, ignore cache')
    parser.add_argument('--clear-cache', action='store_true',
                        help='Clear cached results for this specific analysis')
    parser.add_argument('--list-cache', action='store_true',
                        help='List all cached analyses and exit')
    parser.add_argument('--clear-all-cache', action='store_true',
                        help='Clear all cached data and exit')
    
    args = parser.parse_args()
    
    # Handle cache listing
    if args.list_cache:
        cached = list_cached_analyses()
        print("\n📦 Cached Analyses:")
        print("=" * 70)
        if not cached:
            print("   No cached analyses found.")
        else:
            for c in cached:
                repo_name = os.path.basename(c['repo'])
                print(f"\n   📁 {repo_name}")
                print(f"      Path: {c['repo']}")
                print(f"      Since: {c['since'] or 'All time'}")
                print(f"      Files: {c['files']}")
                print(f"      Cached: {c['cached_at']}")
        print(f"\n   Cache location: {CACHE_DIR}")
        sys.exit(0)
    
    # Handle clear all cache
    if args.clear_all_cache:
        clear_cache(clear_all=True)
        print("✅ All cached analyses cleared.")
        sys.exit(0)
    
    # Show exclusions and exit if requested
    if args.show_excludes:
        print("\n📋 Default Exclusion Patterns:")
        print("=" * 50)
        for pattern in sorted(DEFAULT_EXCLUSIONS):
            print(f"   {pattern}")
        print(f"\nTotal: {len(DEFAULT_EXCLUSIONS)} patterns")
        print("\nUse --exclude 'pattern' to add more patterns")
        print("Use --no-default-excludes to disable these defaults")
        sys.exit(0)
    
    # Require repo_path for analysis
    if not args.repo_path:
        parser.error("repo_path is required for analysis")
    
    # Build exclusion list
    if args.no_default_excludes:
        exclusions = list(args.exclude)
    else:
        exclusions = DEFAULT_EXCLUSIONS + list(args.exclude)
    
    # Validate repository path
    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: '{repo_path}' is not a valid directory")
        sys.exit(1)
    
    git_dir = os.path.join(repo_path, '.git')
    if not os.path.isdir(git_dir):
        print(f"Error: '{repo_path}' is not a git repository (no .git directory)")
        sys.exit(1)
    
    # Handle clear cache for specific analysis
    if args.clear_cache:
        if clear_cache(repo_path, args.since, exclusions):
            print(f"✅ Cache cleared for {repo_path}")
        else:
            print(f"ℹ️  No cache found for {repo_path}")
    
    # Set up output directory
    output_dir = args.output or os.path.join(os.getcwd(), 'hotspot_output')
    os.makedirs(output_dir, exist_ok=True)
    
    print("\n🔍 Code Hotspot Analyzer")
    print("=" * 50)
    print(f"📁 Repository: {repo_path}")
    if args.since:
        print(f"📅 Analyzing commits since: {args.since}")
    if args.exclude:
        print(f"🚫 Additional exclusions: {', '.join(args.exclude)}")
    if args.no_default_excludes:
        print("⚠️  Default exclusions disabled")
    print()
    
    # Check cache if enabled
    use_cache = args.cache and not args.no_cache
    cached_data = None
    cache_valid = False
    
    if use_cache:
        print("📦 Checking cache...")
        cached_data, cache_valid = load_cache(repo_path, args.since, exclusions)
        
        if cache_valid:
            print("   ✅ Valid cache found! Using cached results.")
            print(f"   📅 Cached at: {cached_data.get('cached_at', 'Unknown')}")
            data = cached_data
            hotspots = data['hotspots']
            hierarchy = data['hierarchy']
        elif cached_data:
            print("   ⚠️  Cache exists but repo has changed. Re-analyzing...")
        else:
            print("   ℹ️  No cache found. Running fresh analysis...")
    
    # Run analysis if cache not valid
    if not cache_valid:
        # Step 1: Get revision frequency
        print("📊 Analyzing revision frequency...")
        revisions = get_revision_frequency(repo_path, args.since, exclusions)
        print(f"   Found {len(revisions)} files with revision history")
        
        # Step 2: Count lines of code
        print("📏 Counting lines of code...")
        loc = count_lines_of_code(repo_path, exclusions)
        print(f"   Analyzed {len(loc)} files")
        
        # Step 3: Get additional metrics
        print("👥 Analyzing author distribution...")
        authors = get_author_count(repo_path, args.since, exclusions)
        
        print("📈 Calculating code churn...")
        churn = get_churn_data(repo_path, args.since, exclusions)
        
        print("💬 Collecting commit messages...")
        commit_messages = get_commit_messages(repo_path, args.since, exclusions)
        print(f"   Found commits for {len(commit_messages)} files")
        
        # Step 4: Calculate hotspots
        print("🔥 Calculating hotspots...")
        hotspots = calculate_hotspots(revisions, loc, churn, authors)
        print(f"   Identified {len(hotspots)} files for analysis")
        
        # Add commit messages to hotspots
        for h in hotspots:
            h['commits'] = commit_messages.get(h['file'], [])
        
        if not hotspots:
            print("\n⚠️  No hotspots found. This could mean:")
            print("   - The repository has very few commits")
            print("   - All files are very small (< 10 lines)")
            print("   - The --since date is too recent")
            sys.exit(1)
        
        # Step 5: Build hierarchy for visualization
        print("🌳 Building visualization hierarchy...")
        hierarchy = build_hierarchy(hotspots)
        
        # Step 6: Save data
        data = {
            'repository': repo_path,
            'analyzed_at': datetime.now().isoformat(),
            'since_date': args.since,
            'hotspots': hotspots,
            'hierarchy': hierarchy,
            'commit_messages': commit_messages  # Store separately for easy lookup
        }
        
        # Save to cache
        if use_cache or args.cache:
            cache_path = save_cache(repo_path, args.since, exclusions, data.copy())
            if cache_path:
                print(f"📦 Results cached to: {cache_path}")
    
    # Save to output directory
    json_path = os.path.join(output_dir, 'hotspot_data.json')
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"💾 Data saved to: {json_path}")
    
    # Step 7: Generate HTML
    html_path = generate_html(output_dir)
    print(f"📄 Visualization created: {html_path}")
    
    # Print top hotspots
    print("\n🔥 Top 10 Hotspots:")
    print("-" * 60)
    for i, h in enumerate(hotspots[:10], 1):
        score_bar = "█" * int(h['hotspot_score'] * 20)
        print(f"{i:2}. {h['file'][:45]:<45}")
        print(f"    Revisions: {h['revisions']:>4} | Lines: {h['lines']:>5} | Score: {score_bar}")
    print()
    
    # Step 8: Start server and open browser (unless --no-serve)
    if args.no_serve:
        print(f"\n✅ Analysis complete! Open {html_path} in a browser to view.")
        print("   (Tip: You may need to serve the files via HTTP for the visualization to work)")
        print(f"   Quick start: cd {output_dir} && python3 -m http.server {args.port}")
        return
    
    if not args.no_open:
        url = f"http://localhost:{args.port}"
        
        # Open browser after a short delay
        def open_browser():
            import time
            time.sleep(1)
            webbrowser.open(url)
        
        browser_thread = threading.Thread(target=open_browser)
        browser_thread.daemon = True
        browser_thread.start()
    
    # Start the server
    try:
        start_server(output_dir, args.port)
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped. Goodbye!")


if __name__ == '__main__':
    main()

