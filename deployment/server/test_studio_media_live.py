"""Explicit opt-in: one real image and one six-second video, on an isolated visitor.

Run from a client machine, not a local backend. The two requests reserve CNY 2.025
at the configured default prices. Does not retry paid submissions.
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:18100")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Refresh saved results/assets with GET only; no generation",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", choices=("image", "video", "both"), default="both")
    parser.add_argument("--book-id", default="50eade62e574")
    parser.add_argument("--page", type=int, default=8)
    parser.add_argument(
        "--excerpt",
        default="Phoenix flutes make music, The moonlight flashes, Fish and dragon lanterns whirl the whole night long.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the isolated identity and preserve existing results",
    )
    args = parser.parse_args()
    if not args.confirm_live and not args.collect_only:
        parser.error("--confirm-live is required")
    args.resume = args.resume or args.collect_only
    destination = Path(args.output)
    if destination.exists() and not args.resume:
        parser.error("Output already exists; use a new path or explicitly --resume")
    cookies = (
        json.loads((destination / "cookies.json").read_text()) if args.resume else {}
    )
    with httpx.Client(base_url=args.base, timeout=30, cookies=cookies) as client:
        client.get("/api/auth/me").raise_for_status()
        catalog = client.get("/api/library").json()
        book = next(b for b in catalog if b["book_id"] == args.book_id)
        output = (
            json.loads((destination / "result.json").read_text())
            if args.resume
            else {"book_id": book["book_id"], "jobs": []}
        )
        if output["book_id"] != args.book_id:
            parser.error("Cannot change book when resuming")
        destination.mkdir(parents=True, exist_ok=True)
        # Private isolated-test identity enables read-only playback checks without regeneration.
        with os.fdopen(
            os.open(
                destination / "cookies.json",
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            ),
            "w",
        ) as file:
            json.dump(dict(client.cookies), file)
        for kind in (
            ()
            if args.collect_only
            else (("image", "video") if args.kind == "both" else (args.kind,))
        ):
            response = client.post(
                "/api/studio/media",
                json={
                    "book_id": book["book_id"],
                    "pages": [args.page],
                    "excerpt": args.excerpt,
                    "kind": kind,
                    "goal": "意思",
                    "level": "入门",
                    "request_id": uuid.uuid4().hex,
                    "consent": True,
                },
            )
            response.raise_for_status()
            output["jobs"].append(response.json())
            (destination / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
            print(kind, response.json()["id"], "submitted", flush=True)
            previous = None
            deadline = time.monotonic() + 1200
            job = output["jobs"][-1]
            while time.monotonic() < deadline:
                response = client.get("/api/studio/jobs/" + job["id"])
                response.raise_for_status()
                value = response.json()
                output["jobs"][-1] = value
                if previous != value["status"]:
                    print(value["kind"], value["status"], value["error"], flush=True)
                    previous = value["status"]
                if value["status"] in {"succeeded", "failed", "uncertain"}:
                    break
                time.sleep(8)
            (destination / "result.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2)
            )
            if output["jobs"][-1]["status"] != "succeeded":
                print(
                    "Stopping: do not spend on another generation after an unsuccessful job.",
                    flush=True,
                )
                break
        for i, old_job in enumerate(output["jobs"]):
            response = client.get("/api/studio/jobs/" + old_job["id"])
            response.raise_for_status()
            job = response.json()
            output["jobs"][i] = job
            if job["asset_url"]:
                asset = client.get(job["asset_url"])
                asset.raise_for_status()
                extension = "jpg" if job["kind"] == "image" else "mp4"
                (destination / f"{job['id']}.{extension}").write_bytes(asset.content)
        (destination / "result.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2)
        )
        print(
            "Result saved. Successful:",
            sum(j["status"] == "succeeded" for j in output["jobs"]),
            "/",
            len(output["jobs"]),
            flush=True,
        )


if __name__ == "__main__":
    main()
