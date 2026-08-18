#!/usr/bin/env python3
"""Test script for verifying batch cosine SIMD changes - simplified."""

import sys
import ast
import inspect

print('Testing syntax and structure of modified files...')

# Test 1: identity_stitching.py
print('\n1. Checking identity_stitching.py...')
with open('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/recon/identity_stitching.py', 'r') as f:
    source = f.read()

try:
    tree = ast.parse(source)
    print('   ✓ Syntax is valid')
except SyntaxError as e:
    print(f'   ✗ Syntax error: {e}')
    sys.exit(1)

# Check for key elements
has_numpy_import = 'import numpy as np' in source
has_batch_method = '_batch_cosine_scores_npy' in source
has_rust_npy_call = 'batch_cosine_scores_npy' in source
has_batch_face_usage = 'scores = self._batch_cosine_scores_npy' in source

print(f'   - numpy import: {"✓" if has_numpy_import else "✗"}')
print(f'   - _batch_cosine_scores_npy method: {"✓" if has_batch_method else "✗"}')
print(f'   - batch_cosine_scores_npy Rust call: {"✓" if has_rust_npy_call else "✗"}')
print(f'   - Batch usage in _compute_face_signal: {"✓" if has_batch_face_usage else "✗"}')

if not all([has_numpy_import, has_batch_method, has_rust_npy_call, has_batch_face_usage]):
    print('   ✗ identity_stitching.py missing required components')
    sys.exit(1)

# Test 2: document_intelligence.py
print('\n2. Checking document_intelligence.py...')
with open('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/recon/document_intelligence.py', 'r') as f:
    source = f.read()

try:
    tree = ast.parse(source)
    print('   ✓ Syntax is valid')
except SyntaxError as e:
    print(f'   ✗ Syntax error: {e}')
    sys.exit(1)

# Check for key elements
has_numpy_import = 'import numpy as np' in source
has_batch_method = '_batch_cosine_scores_npy' in source
has_rust_npy_call = 'batch_cosine_scores_npy' in source
has_batch_semantic_usage = 'scores = self._batch_cosine_scores_npy' in source

print(f'   - numpy import: {"✓" if has_numpy_import else "✗"}')
print(f'   - _batch_cosine_scores_npy method: {"✓" if has_batch_method else "✗"}')
print(f'   - batch_cosine_scores_npy Rust call: {"✓" if has_rust_npy_call else "✗"}')
print(f'   - Batch usage in _compute_semantic_score: {"✓" if has_batch_semantic_usage else "✗"}')

if not all([has_numpy_import, has_batch_method, has_rust_npy_call, has_batch_semantic_usage]):
    print('   ✗ document_intelligence.py missing required components')
    sys.exit(1)

print('\n✅ All structural tests passed!')
print('\nNote: Full import tests require proper hledac namespace setup.')
print('The syntactic and structural changes have been verified.')
