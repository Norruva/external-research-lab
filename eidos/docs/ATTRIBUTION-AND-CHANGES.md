BANKING77: PolyAI, Casanueva et al., Efficient Intent Detection with Dual Sentence Encoders, ACL ConvAI2020. Source commit57ec275d8078af65b7731c2a98be812d844a6d6b, CC-BY-4.0. EIDOS independently fits sparse features and weights from the training split. The dataset is not exclusive proprietary material. No LAYA source, weights or per-case outputs are included. Aggregate comparison metrics are recorded in EVALUATION.json.

Full77 evaluation child: added aggregate secondary comparison and documentation/static-notice updates. Owned model and executable source unchanged; public training-data attribution retained.

Tournament evaluation documentation child: added the exact aggregate result and scoped report; updated README and manifest. All executable/client/model/HTML and prior evaluation bytes are unchanged. Public data attribution is retained; no LAYA/REA implementation, tensors or teacher outputs enter EIDOS.


## Cloud readiness child release, 21 October 2026

Derived from the exact EIDOS tournament application parent with SHA-256
`e026efd2648484e545adab14c23a617d25b62b230e0ca37fcce316e6f801a60c`.
Only `eidos/service.py` changes executable behavior: the existing readiness
request is applied through FIFO admission once per pool worker. The five-second
readiness timeout, request deadlines, failure propagation and registry pins
remain intact. README and manifest describe and bind this serving-only change.
All other parent members are byte-identical, including model/classifier/browser,
public-data attribution and prior evaluation reports. No retraining, reference
inference, quality comparison or third-party code addition accompanies packaging.
The FIFO sweep concerns sequential same-registry startup; it does not guarantee
coverage during concurrent mixed-registry swaps or after worker recycling.
Existing ownership, licensing and CC-BY-4.0 attribution remain applicable.


## Worker lifecycle child release, 21 October 2026

Derived from cloud-readiness archive SHA-256 6853df7e585ab900d20ce5b1b45d3c5a7092d306bd406da07059298d09315114. Executable changes are confined to eidos/pool.py and eidos/service.py: explicit preparation, bounded controller-created background readiness, cold-decision admission refusal and graceful lifecycle shutdown. Request deadlines, classifier/training/model/browser/worker code and all prior evaluation payloads remain byte-identical. README and manifest describe and bind the change. Earlier notes above describe their historical parent releases. No new quality comparison, fitting, teacher outputs, third-party implementation or reference-model execution accompanies this change. Existing ownership and public CC-BY-4.0 attribution remain applicable.

## Optional CLINC150 profile

Stefan Larson et al., An Evaluation Dataset for Intent Classification and Out-of-Scope Prediction, EMNLP-IJCNLP2019, https://www.aclweb.org/anthology/D19-1131/. clinc/oos-eval commit828f8093932c8fe6ca7936c3d2e52903b1c523de, CC BY3.0 Unported (CLINC150-SOURCE-LICENSE.txt; https://creativecommons.org/licenses/by/3.0/). EIDOS curates train/development roles, quarantines7 lower-priority conflicting/overlapping rows and independently fits lexical TF-IDF/linear weights; no official test rows removed. Licensed source data are not exclusive property. No endorsement by source authors. Parent runtime/model/evaluation bytes retained; generic HTML notice/placeholder and README updated for explicit profile selection. No deployment/promotion or new reference/JEV run.

## Optional temperature and scope extension

Original stdlib scalar temperature calibration and scope-threshold selection use held-out CLINC development roles from the same attributed CC BY3.0 source. Model weights/features/IDF and both parent profiles are unchanged. Positive temperature scales probabilities; no accuracy gain or new rival comparison claimed. Added model/policy, scope CLI/library and aggregate evidence; README/attribution changed, all parent Python/JS/TS/CSS/schema/HTML preserved. All outputs remain review-only. The official OOS test is now revealed. No source-author endorsement, exclusive-data ownership or production certification.

## Original support-memory extension

Original sparse top5cosine/logmeanexp runtime, checked model format/dispatch and nearest-support CLI; fresh linear control on identical3020labeled supports. Added151-label optional profiles, source controls and aggregate evaluation. SNIPS CC0-1.0 source and Coucke et al.2018 attribution above; CLINC CC BY3.0 source and Larson et al.2019 attribution retained. Existing model tensors, service/worker/browser/API/schema are unchanged; README, attribution and model-format dispatch changed. No rival tensors/implementation, exclusive-data ownership or author endorsement. All new outputs abstain/review.

## Owned-data support training CLI

Original standard-library support-only TFIDF/model builder with strict labeled input/provenance/bounds, deterministic private artifacts and no activation. Added40authored synthetic CC0-1.0 demo rows/provenance/request,TRAINING.md and authored mechanical tests. These fixtures are not independent quality evidence. All five preinstalled model artifacts and the original inference/service/worker/browser/API/schema are unchanged. README/attribution changed. Public licensed corpora remain attributed and are not exclusive property. New artifacts are review-only and uncalibrated; no model promotion or production certification.
