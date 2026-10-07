"""Pure condition engine checks, no HTTP connections required."""
import unittest
from app.rules import evaluate_condition, resolve_expression
class ConditionTests(unittest.TestCase):
    def test_transition(self):
        ctx={'current':{'presence':{'user':'JGRA'}},'previous':{'presence':{'user':None}}}
        self.assertTrue(evaluate_condition({'kind':'condition','left':'current.presence.user','operator':'changed'},ctx))
    def test_expression_transforms(self):
        ctx={
            'current':{'presence':{
                'connected_workstations':['ESC-R1','ESC-R2'],
                'workstation_users':{'ESC-R1':['JGRA']},
                'local_time':'17:30:00',
            }},
            'previous':{'presence':{
                'connected_workstations':['ESC-R1'],
                'workstation_users':{'ESC-R1':['OLD']},
                'local_time':'16:30:00',
            }},
        }
        expr={'source':'current.presence.connected_workstations','transforms':[{'op':'count'}]}
        self.assertEqual(resolve_expression(expr,ctx),2)
        self.assertTrue(evaluate_condition({'kind':'condition','left':expr,'operator':'gt','right':1},ctx))
        user_expr={'source':'current.presence.workstation_users','transforms':[{'op':'key','arg':'ESC-R1'},{'op':'first'}]}
        self.assertTrue(evaluate_condition({'kind':'condition','left':user_expr,'operator':'eq','right':'JGRA'},ctx))
        self.assertTrue(evaluate_condition({'kind':'condition','left':user_expr,'operator':'changed'},ctx))
        time_expr={'source':'current.presence.local_time','transforms':[{'op':'as_time'}]}
        self.assertTrue(evaluate_condition({'kind':'condition','left':time_expr,'operator':'gt','right':'17:00:00'},ctx))

    def test_group(self):
        ctx={'current':{'presence':{'user':'JGRA'},'queue':{'count':3}},'previous':{}}
        self.assertTrue(evaluate_condition({'kind':'group','logic':'all','items':[{'kind':'condition','left':'current.presence.user','operator':'eq','right':'JGRA'},{'kind':'condition','left':'current.queue.count','operator':'gt','right':1}]},ctx))
if __name__=='__main__':unittest.main()