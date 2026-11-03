import json
from pathlib import Path
import tempfile
import unittest
from config_toolkit.core.project import load_project
from config_toolkit.core.planning import compile_recipe, preview_step


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        profile = {'schema_version':1,'project_id':'unit','data_roots':{'local':'data'},'state_dir':'state',
                   'excel':{'layout':{'field_row':1,'data_start_row':2}}}
        project_path = self.root / 'project.json'
        project_path.write_text(json.dumps(profile), encoding='utf-8')
        self.project = load_project(project_path)

    def recipe(self, value):
        path = self.root / 'recipe.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_typed_parameters_and_order_are_data_only(self):
        recipe = self.recipe({'schema_version':1,'id':'test','parameters':{'count':{'type':'int'}},
            'steps':[{'name':'second','depends_on':['first'],'request':{'value':'{{count}}'}},
                     {'name':'first','request':{'text':'literal $(not_executed)'}}]})
        plan = compile_recipe(self.project,'local',recipe,{'count':7})
        self.assertEqual([item['name'] for item in plan['steps']],['first','second'])
        self.assertEqual(plan['steps'][1]['request']['value'],7)
        self.assertEqual(plan['steps'][0]['request']['text'],'literal $(not_executed)')
        self.assertFalse(plan['write_permission'])
        self.assertEqual(list((self.root/'data').iterdir()),[])

    def test_cycle_and_unknown_parameter_are_rejected(self):
        recipe = self.recipe({'schema_version':1,'id':'test','parameters':{},'steps':[
            {'name':'a','depends_on':['b'],'request':{}},{'name':'b','depends_on':['a'],'request':{}}]})
        with self.assertRaises(ValueError):
            compile_recipe(self.project,'local',recipe,{})
        with self.assertRaises(ValueError):
            compile_recipe(self.project,'local',recipe,{'extra':'value'})

    def test_tampered_plan_is_rejected_before_source_read(self):
        recipe = self.recipe({'schema_version':1,'id':'test','steps':[{'name':'one','request':{}}]})
        plan = compile_recipe(self.project,'local',recipe,{})
        plan['steps'][0]['request']['file']='changed.xlsx'
        with self.assertRaises(ValueError):
            preview_step(self.project,'local',plan,'one')


if __name__ == '__main__':
    unittest.main()
