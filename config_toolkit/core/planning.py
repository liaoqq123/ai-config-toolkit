"""Data-only recipe expansion; plans never approve or apply changes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                      separators=(',', ':')).encode('utf-8')).hexdigest()


def compile_recipe(project, root_name, recipe_path, answers):
    project.root(root_name)
    recipe = json.loads(Path(recipe_path).read_text(encoding='utf-8-sig'))
    if recipe.get('schema_version') != 1 or not isinstance(recipe.get('id'), str):
        raise ValueError('配方需要 schema_version=1 和明确的 id。')
    definitions = recipe.get('parameters', {})
    if not isinstance(answers, dict) or set(answers) - set(definitions):
        raise ValueError('答案必须是参数字典，且不能包含未声明的参数。')
    values = {}
    for name, spec in definitions.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or not isinstance(spec, dict):
            raise ValueError('配方参数定义无效。')
        if name not in answers and 'default' not in spec:
            if spec.get('required', True):
                raise ValueError('缺少配方参数：' + name)
            continue
        value = answers.get(name, spec.get('default'))
        kind = spec.get('type', 'string')
        valid = {'string':lambda x:isinstance(x,str), 'int':lambda x:type(x) is int,
                 'number':lambda x:type(x) in (int,float), 'bool':lambda x:type(x) is bool,
                 'array':lambda x:isinstance(x,list), 'object':lambda x:isinstance(x,dict)}
        if kind not in valid or not valid[kind](value):
            raise ValueError('配方参数类型不符：' + name)
        values[name] = value
    pattern = re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}')
    def expand(value):
        if isinstance(value, dict):
            return {key:expand(item) for key,item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, str):
            return deepcopy(value)
        match = pattern.fullmatch(value)
        if match:
            if match[1] not in values:
                raise ValueError('模板引用未提供的参数：' + match[1])
            return deepcopy(values[match[1]])
        def replace(match):
            if match[1] not in values or not isinstance(values[match[1]], (str,int,float,bool)):
                raise ValueError('文本占位符需要已提供的标量参数：' + match[1])
            return str(values[match[1]])
        return pattern.sub(replace, value)
    steps = recipe.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError('配方至少需要一个明确的步骤。')
    by_name = {}
    for step in steps:
        name = step.get('name') if isinstance(step, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', name) or name in by_name:
            raise ValueError('步骤名缺失、无效或重复。')
        if not isinstance(step.get('request'), dict):
            raise ValueError('每个步骤必须声明完整 request。')
        by_name[name] = step
    ordered, remaining = [], list(by_name)
    while remaining:
        ready = []
        for name in remaining:
            dependencies = by_name[name].get('depends_on', [])
            if not isinstance(dependencies, list) or any(item not in by_name for item in dependencies):
                raise ValueError('配方依赖指向未声明的步骤。')
            if all(item in [step['name'] for step in ordered] for item in dependencies):
                ready.append(name)
        if not ready:
            raise ValueError('配方步骤存在循环依赖。')
        for name in ready:
            ordered.append({'name':name, 'depends_on':by_name[name].get('depends_on', []),
                            'request':expand(by_name[name]['request'])})
            remaining.remove(name)
    result = {'schema_version':1, 'kind':'configuration_plan', 'project_id':project.project_id,
              'root':root_name, 'recipe_id':recipe['id'], 'steps':ordered,
              'write_permission':False}
    result['plan_digest'] = digest(result)
    return result


def preview_step(project, root_name, plan, name):
    from .workflow import Workflow
    raw = {key:value for key,value in plan.items() if key != 'plan_digest'}
    if (plan.get('plan_digest') != digest(raw) or plan.get('project_id') != project.project_id
            or plan.get('root') != root_name or plan.get('kind') != 'configuration_plan'):
        raise ValueError('计划摘要、项目或数据根不匹配，请重新编译。')
    steps = [step for step in plan['steps'] if step['name'] == name]
    if len(steps) != 1:
        raise ValueError('步骤不存在或不唯一。')
    # Dependencies describe order; each subsequent step still needs a fresh
    # preview, human approval, and apply receipt. Compilation grants none.
    return Workflow(project, root_name).preview(steps[0]['request'])
