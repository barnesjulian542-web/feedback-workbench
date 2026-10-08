"""用于理解审核暂停、退出进程后恢复、重复提交。"""
import argparse
import json

from workflow import open_graph, start_feedback, resume_feedback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("text")
    start.add_argument("--source-id", default="")
    resume = sub.add_parser("resume")
    resume.add_argument("feedback_id")
    resume.add_argument("--action", choices=["create", "link", "reject"], default="create")
    resume.add_argument("--requirement-id", default=None)
    sub.add_parser("list")
    args = parser.parse_args()
    with open_graph(args.data_dir) as (store, graph):
        if args.command == "start":
            fid, snapshot = start_feedback(store, graph, args.text, args.source_id)
            payload = {"feedback_id": fid, "next": list(snapshot.next), "state": snapshot.values}
        elif args.command == "resume":
            payload = resume_feedback(store, graph, args.feedback_id,
                                      {"action": args.action, "requirement_id": args.requirement_id})
        else:
            payload = store.list_requirements()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
