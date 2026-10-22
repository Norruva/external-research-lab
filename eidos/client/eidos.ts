// EIDOS v1.0. Authorization is never conveyed by this recommendation contract.
export type Json = null | boolean | number | string | Json[] | {[key:string]:Json};
export interface Choice { id:string; class_id:string }
export interface QuestionBase { id:string; profile:string; prompt:string; state_path:string[] }
export type Question =
 | (QuestionBase & {kind:"CHOICE"|"ROUTE";choices:Choice[]})
 | (QuestionBase & {kind:"BINARY";positive_class:string;negative_class:string})
 | (QuestionBase & {kind:"SCORE";anchors:{class_id:string;value:number}[];minimum:number;maximum:number;value_type:"number"|"integer"})
 | {id:string;kind:"ABSTAIN";reason:string};
export interface DecisionRequest { schema_version:"1.0";input_version:string;request_id:string;tenant_id:string;language:string;state:Json;sources:{id:string;uri?:string;sha256?:string}[];questions:Question[];budget_ms?:number }
export interface ModelRef {id:string;version:string;sha256:string;feature_version:string;calibration_status:"unfitted"|"temperature_fitted_synthetic_only"|"temperature_fitted_heldout_scope_only"}
interface DecisionBase {id:string;kind:Question["kind"];status:"answered"|"abstained";value:string|number|boolean|null;candidate_value:string|number|boolean|null;distribution:{id:string;class_id:string;value:string|number|boolean|null;logit:number;probability:number}[];confidence:number|null;calibrated_confidence:null;uncertainty:{entropy?:number;margin?:number;lexical_coverage?:number;score_stddev?:number};review_required:boolean;reasons:string[];model:ModelRef|null;evidence_refs:string[];evidence_scope:"caller_supplied_unverified"}
export type Decision = Omit<DecisionBase,"kind"|"value"|"candidate_value"> & (
 | {kind:"CHOICE"|"ROUTE";value:string|null;candidate_value:string|null}
 | {kind:"BINARY";value:boolean|null;candidate_value:boolean|null}
 | {kind:"SCORE";value:number|null;candidate_value:number|null}
 | {kind:"ABSTAIN";value:null;candidate_value:null;status:"abstained"}
);
export interface DecisionResponse {schema_version:"1.0";input_version:string;request_id:string;trace_id:string;tenant_id:string;timestamp:string;decisions:Decision[];authorization:"recommendation_only_no_action_authorized";execution:{engine_version:"0.2.0";duration_ms:number;network_used:false;payload_logged:false}}
// Transport is supplied by the authenticated host application; no implicit network or auth.
export async function decide(request:DecisionRequest, transport:(r:DecisionRequest)=>Promise<DecisionResponse>):Promise<DecisionResponse> {return transport(request);}
