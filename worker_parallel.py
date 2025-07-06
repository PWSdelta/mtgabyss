#!/usr/bin/env python3
"""
MTGAbyss Parallel Worker Launcher
=================================

Launches multiple instances of the unified worker in parallel for faster processing.
Supports both Gemini and Ollama providers with full CLI argument forwarding.
"""

import subprocess
import datetime
import os
import sys
import argparse
import time

def main():
    parser = argparse.ArgumentParser(
        description="Launch multiple MTGAbyss workers in parallel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch 2 Gemini workers
  python worker_parallel.py --provider gemini --workers 2
  
  # Launch 3 Ollama workers with custom model
  python worker_parallel.py --provider ollama --model llama3.1:8b --workers 3
  
  # Launch workers with limits and custom batch size
  python worker_parallel.py --provider gemini --workers 2 --limit 50 --batch-size 3
        """
    )
    
    # Worker configuration
    parser.add_argument('--workers', 
                       type=int, 
                       default=2,
                       help='Number of parallel workers (default: 2)')
    
    parser.add_argument('--provider', 
                       choices=['gemini', 'ollama'], 
                       required=True,
                       help='LLM provider to use for all workers')
    
    parser.add_argument('--model', 
                       default=None,
                       help='Model name (default: gemini-1.5-flash for Gemini, llama3.1:8b for Ollama)')
    
    parser.add_argument('--limit', 
                       type=int, 
                       default=None,
                       help='Total cards to process (split among workers)')
    
    parser.add_argument('--batch-size', 
                       type=int, 
                       default=5,
                       help='Batch size per worker (default: 5)')
    
    parser.add_argument('--rate-limit', 
                       type=float, 
                       default=1.0,
                       help='Rate limit per worker (default: 1.0)')
    
    parser.add_argument('--quiet', 
                       action='store_true',
                       help='Hide worker analysis output')
    
    parser.add_argument('--stagger', 
                       type=float, 
                       default=2.0,
                       help='Seconds to wait between starting workers (default: 2.0)')
    
    args = parser.parse_args()
    
    # Use the current Python executable (should be venv if activated)
    python_executable = sys.executable
    
    # Calculate per-worker limits if total limit specified
    per_worker_limit = None
    if args.limit:
        per_worker_limit = max(1, args.limit // args.workers)
        remainder = args.limit % args.workers
    
    print(f"""
MTGAbyss Parallel Worker Launcher
=================================
Workers: {args.workers}
Provider: {args.provider}
Model: {args.model or 'default'}
Total Limit: {args.limit or 'unlimited'}
Per-Worker Limit: {per_worker_limit or 'unlimited'}
Batch Size: {args.batch_size}
Rate Limit: {args.rate_limit}s
Stagger Delay: {args.stagger}s

Starting workers...
""")
    
    # Start worker processes
    procs = []
    for i in range(args.workers):
        # Build command for unified worker
        cmd = [python_executable, "-u", "unified_worker.py"]
        cmd.extend(["--provider", args.provider])
        
        if args.model:
            cmd.extend(["--model", args.model])
        
        # Set per-worker limit
        worker_limit = per_worker_limit
        if args.limit and i < remainder:
            worker_limit += 1  # Distribute remainder among first workers
        
        if worker_limit:
            cmd.extend(["--limit", str(worker_limit)])
        
        cmd.extend(["--batch-size", str(args.batch_size)])
        cmd.extend(["--rate-limit", str(args.rate_limit)])
        
        if args.quiet:
            cmd.append("--quiet")
        
        print(f"Worker {i+1}: {' '.join(cmd[2:])}")  # Show args without python path
        print(f"Starting at {datetime.datetime.now().isoformat()}")
        
        # Start the process
        p = subprocess.Popen(cmd)
        procs.append(p)
        
        # Stagger worker starts to avoid API conflicts
        if i < args.workers - 1 and args.stagger > 0:
            print(f"Waiting {args.stagger}s before starting next worker...")
            time.sleep(args.stagger)
    
    print(f"\nAll {args.workers} workers started. Press Ctrl+C to stop all workers.\n")
    
    try:
        # Wait for all workers to complete
        for i, p in enumerate(procs):
            print(f"Waiting for worker {i+1} (PID {p.pid}) to complete...")
            p.wait()
            print(f"Worker {i+1} finished with exit code {p.returncode}")
    
    except KeyboardInterrupt:
        print("\nStopping all workers...")
        for i, p in enumerate(procs):
            try:
                p.terminate()
                print(f"Terminated worker {i+1} (PID {p.pid})")
            except:
                pass
        
        # Wait a bit for graceful shutdown
        time.sleep(2)
        
        # Force kill any remaining processes
        for i, p in enumerate(procs):
            if p.poll() is None:
                try:
                    p.kill()
                    print(f"Force killed worker {i+1} (PID {p.pid})")
                except:
                    pass
    
    print("All workers stopped.")

if __name__ == "__main__":
    main()