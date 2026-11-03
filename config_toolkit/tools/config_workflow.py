"""Preview, approve, apply and recover one explicit physical table at a time."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from config_toolkit.core.common import ToolError,load_json
from config_toolkit.core.project import load_project
from config_toolkit.core.workflow import Workflow


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",required=True)
    parser.add_argument("--root",required=True)
    commands=parser.add_subparsers(dest="command",required=True)
    preview=commands.add_parser("preview")
    preview.add_argument("--input",required=True)
    approve=commands.add_parser("approve",help="Record an actual human decision; AI must not invent approval.")
    approve.add_argument("--preview",required=True)
    approve.add_argument("--actor",required=True)
    approve.add_argument("--reason",required=True)
    apply=commands.add_parser("apply")
    apply.add_argument("--preview",required=True)
    apply.add_argument("--approval",required=True)
    recover=commands.add_parser("recover")
    recover.add_argument("--transaction",required=True)
    show=commands.add_parser("show")
    show.add_argument("--preview",required=True)
    commands.add_parser("list")
    args=parser.parse_args(argv)
    try:
        engine=Workflow(load_project(args.project),args.root)
        if args.command=="preview":
            result=engine.preview(load_json(args.input))
        elif args.command=="approve":
            result=engine.approve(args.preview,args.actor,args.reason)
        elif args.command=="apply":
            result=engine.apply(args.preview,args.approval)
        elif args.command=="recover":
            result=engine.recover(args.transaction)
        elif args.command=="show":
            result=engine.get_preview(args.preview)
        else:
            result={"status":"listed","previews":engine.list_previews(),"business_writes":0}
        code=0 if result.get("status")!="recovery_required" else 2
    except (ToolError,OSError,ValueError,KeyError,TypeError) as error:
        result={"status":"failed","error":error.as_dict() if isinstance(error,ToolError) else str(error),"business_writes":0}
        code=2
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return code


if __name__=="__main__":
    raise SystemExit(main())
