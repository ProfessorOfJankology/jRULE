"""Pure condition engine checks, no HTTP connections required."""
import unittest
from app.rules import evaluate_condition
class ConditionTests(unittest.TestCase):
    def test_transition(self):
        ctx={'current':{'presence':{'user':'JGRA'}},'previous':{'presence':{'user':None}}}
        self.assertTrue(evaluate_condition({'kind':'condition','left':'current.presence.user','operator':'changed'},ctx))
    def test_group(self):
        ctx={'current':{'presence':{'user':'JGRA'},'queue':{'count':3}},'previous':{}}
        self.assertTrue(evaluate_condition({'kind':'group','logic':'all','items':[{'kind':'condition','left':'current.presence.user','operator':'eq','right':'JGRA'},{'kind':'condition','left':'current.queue.count','operator':'gt','right':1}]},ctx))
if __name__=='__main__':unittest.main()