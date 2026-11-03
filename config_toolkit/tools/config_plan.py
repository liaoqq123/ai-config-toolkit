"""Compile generic recipes and preview individual current steps."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import argparse
import json
from config_toolkit.core.project import load_project
from config_toolkit.core.planning import compile_recipe, preview_step


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--root', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    compile_command = commands.add_parser('compile')
    compile_command.add_argument('--recipe', required=True)
    compile_command.add_argument('--answers', required=True)
    compile_command.add_argument('--output', required=True)
    preview = commands.add_parser('preview')
    preview.add_argument('--plan', required=True)
    preview.add_argument('--step', required=True)
    args = parser.parse_args(argv)
    try:
        project = load_project(args.project)
        if args.command == 'compile':
            output = Path(args.output).resolve()
            if any(output.is_relative_to(project.root(name)) for name in project.config['data_roots']):
                raise ValueError('计划产物必须放在配置数据根之外。')
            answers = json.loads(Path(args.answers).read_text(encoding='utf-8-sig'))
            result = compile_recipe(project, args.root, args.recipe, answers)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise ValueError('输出计划已存在，请使用新文件名以保留审查依据。')
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        else:
            plan = json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
            result = preview_step(project, args.root, plan, args.step)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status':'failed', 'error':str(error), 'business_writes':0}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
