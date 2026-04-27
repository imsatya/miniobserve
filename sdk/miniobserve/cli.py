"""MiniObserve CLI"""
import os
import subprocess
import sys

from .verify import send_integration_hello


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "help"

    if cmd == "start":
        print("🔭 Starting MiniObserve server on http://localhost:7823 ...")
        backend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
        subprocess.run(
            ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7823"],
            cwd=backend_dir,
        )

    elif cmd == "logs":
        import httpx

        resp = httpx.get("http://localhost:7823/api/logs?limit=20")
        logs = resp.json()
        for log in logs["logs"]:
            status = "✓" if not log.get("error") else "✗"
            print(
                f"{status} [{log['provider']}] {log['model']} | {log['latency_ms']:.0f}ms | "
                f"${log['cost_usd']:.6f} | {log['timestamp'][:19]}"
            )

    elif cmd == "stats":
        import httpx

        resp = httpx.get("http://localhost:7823/api/stats")
        s = resp.json()
        print(f"\n📊 MiniObserve Stats")
        print(f"   Total calls:   {s['total_calls']}")
        print(f"   Total tokens:  {s['total_tokens']:,}")
        print(f"   Total cost:    ${s['total_cost_usd']:.4f}")
        print(f"   Avg latency:   {s['avg_latency_ms']:.0f}ms")
        print(f"   Error rate:    {s['error_rate_pct']:.1f}%\n")

    elif cmd == "dashboard":
        import webbrowser

        webbrowser.open("http://localhost:7823")
        print("🌐 Opening dashboard at http://localhost:7823")

    elif cmd == "hello":
        ok, msg, _tid, _lid = send_integration_hello()
        print(msg)
        if not ok:
            sys.exit(1)
        print("\n✓ Integration hello succeeded.")

    elif cmd == "quick":
        old = os.environ.get("MINIOBSERVE_TRACER_BLOCKING_FLUSH")
        os.environ["MINIOBSERVE_TRACER_BLOCKING_FLUSH"] = "1"
        try:
            from .tracer import run_quick_probe

            tid = run_quick_probe()
            print(f"[quick] run_id={tid} (multi-span probe; see dashboard)")
        finally:
            if old is None:
                os.environ.pop("MINIOBSERVE_TRACER_BLOCKING_FLUSH", None)
            else:
                os.environ["MINIOBSERVE_TRACER_BLOCKING_FLUSH"] = old

    else:
        print("MiniObserve - Lightweight LLM Observability")
        print("\nCommands:")
        print("  miniobserve hello      Post one hello row — verify dashboard after first integration")
        print("  miniobserve quick      Post a tiny multi-span probe (Tracer; needs HTTP URL)")
        print("  miniobserve start      Start the server + dashboard")
        print("  miniobserve logs       Show recent LLM calls")
        print("  miniobserve stats      Show aggregate stats")
        print("  miniobserve dashboard  Open web dashboard")


if __name__ == "__main__":
    main()
