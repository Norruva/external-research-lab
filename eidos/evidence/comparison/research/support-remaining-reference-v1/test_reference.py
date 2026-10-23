"""Synthetic response validation; no model/checkpoint/data imports."""
import math,unittest
from control import check_response
from statistics import macro,bootstrap


class ResponseTests(unittest.TestCase):
    def test_valid_and_malformed_response_geometry(self):
        labels=list('abcdefg');check_response([1/7]*7,'g',labels)
        for p,c in [([1.]*7,'a'),([float('nan')]+[0.]*6,'a'),([1.,0,0,0,0,0],'a'),([1.,0,0,0,0,0,0],'missing'),([-1.,2.,0,0,0,0,0],'a')]:
            with self.assertRaises(AssertionError):check_response(p,c,labels)

    def test_macro_false_positives_and_missing_gold_classes(self):
        self.assertAlmostEqual(macro(['a','b'],['a','a'],['a','b']),1/3)
        self.assertAlmostEqual(macro(['a'],['a'],list('abcdefg')),1/7)

    def test_paired_cluster_bootstrap_equal_methods(self):
        labels=list('abcdefg');gold=[{'label':v,'group_hash':v+str(i//2)} for v in labels for i in range(4)]
        predictions=[r['label'] for r in gold];result=bootstrap(gold,predictions,predictions,labels,30,113)
        self.assertEqual(result['accuracy_difference_95_percentile'],[0.,0.]);self.assertEqual(result['fixed_seven_macro_f1_difference_95_percentile'],[0.,0.])
        self.assertEqual(set(result['groups_per_label'].values()),{2})


if __name__=='__main__':unittest.main()
