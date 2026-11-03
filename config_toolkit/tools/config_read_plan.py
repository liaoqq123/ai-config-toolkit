"""Read explicit workbook sources and create schema-only indexes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from config_toolkit.core.common import ToolError
from config_toolkit.core.project import load_project
from config_toolkit.core.excel import build_index,read_table


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",required=True)
    parser.add_argument("--root",required=True)
    commands=parser.add_subparsers(dest="command",required=True)
    index=commands.add_parser("build-index")
    index.add_argument("--output",required=True)
    read=commands.add_parser("read")
    read.add_argument("--file",required=True)
    read.add_argument("--sheet",required=True)
    read.add_argument("--id",action="append",dest="ids")
    args=parser.parse_args(argv)
    try:
        project=load_project(args.project)
        result=build_index(project,args.root,args.output) if args.command=="build-index" else read_table(project,args.root,args.file,args.sheet,args.ids)
        code=0
    except (ToolError,OSError,ValueError,KeyError,TypeError) as error:
        result={"status":"failed","error":error.as_dict() if isinstance(error,ToolError) else str(error),"business_writes":0}
        code=2
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return code


if __name__=="__main__":
    raise SystemExit(main())
