# Class-specific greenfield siting screen v1

Status: implementation note for the 119 Mm3 transport-only class screen.

## Question

The generic greenfield siting sensitivity v1 treated every greenfield module as
a class-agnostic, unlimited transport sink. That answers a distance-only
question but cannot distinguish SAW, PULP, PANEL, PELLET and BIO chains. This
note defines a class-specific follow-up:

- split each 525-node harvest weight into the five facility classes using the
  v5.1 E1/E2c province/class feedstock shares;
- keep existing capacity v3 facilities fixed by class and capacity;
- allow class-specific modules only at positive class feedstock nodes in
  provinces with a published road factor;
- keep module throughput unlimited (no invented capacity);
- choose k = 0..5 modules per class by greedy forward selection with exact
  capacitated transport-LP re-evaluation;
- report the k tradeoff, module locations by class, transport cost, mean
  distance, >=150/>=300 km shares and module share;
- compare with the committed generic siting screen.

## Method

Inputs are the committed supply nodes, national facility capacity v3,
transport cost v1 curve, road-distance factors v1, and national product chain
v5 (the E1/E2c class feedstock layer used by facility chain v5.1).

For each province and class, the v5 class feedstock volume is divided by the
province's five-class feedstock total. The resulting share is applied to each
node harvest weight. Shares sum to one, so the class split conserves the node
harvest and each province/class total. The split is a proportional screening
allocation of harvest, not a mass-balance routing of harvest residue.

Existing facilities are the same 591 operating capacity-v3 facilities used by
assignment v10, partitioned by class and held at their committed capacity
(including the documented 1.2x provincial floor scaling). The k-series solves
one transportation LP per class: node class demand equalities, existing
facility capacity upper bounds, unlimited module columns, and a 2026 CAD
transport-cost objective. Distances are haversine x the destination province
road factor. There is no local-preference penalty and no full OSRM routing.

## Labels and limits

`transport_only_siting_screen`, `not_investment_advice`, `model_estimate`,
`observed=false`, `policy_grade=false`. No capital, operating, permitting,
technology, landbase, class-capacity, national, policy, UI or official OSeCAN
claim is made. The class modules are screening sinks, not facility designs.
