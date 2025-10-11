#!/usr/bin/env python

import os
import tempfile
from agent.tools import search_context, read_file, write_file

def test_write_and_read():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        test_file = f.name
    
    try:
        success, msg = write_file(test_file, "Hello, World!")
        assert success, f"Write failed: {msg}"
        print(f"✓ Write test passed: {msg}")
        
        success, content = read_file(test_file)
        assert success, f"Read failed: {content}"
        assert content == "Hello, World!", f"Content mismatch: {content}"
        print(f"✓ Read test passed")
    finally:
        if os.path.exists(test_file):
            os.remove(test_file)

def test_search():
    success, result = search_context("def run_agent", ".")
    assert success, f"Search failed: {result}"
    assert "agent/core.py" in result or "No matches" in result
    print(f"✓ Search test passed")

def test_read_nonexistent():
    success, msg = read_file("/nonexistent/file.txt")
    assert not success, "Should fail on nonexistent file"
    print(f"✓ Nonexistent file test passed")

if __name__ == "__main__":
    print("Running basic tests...\n")
    test_write_and_read()
    test_search()
    test_read_nonexistent()
    print("\n✅ All tests passed!")
