# Extracted Markdown From phase2_ua06451.ipynb

Source gist: `https://gist.github.com/gparadis/bcc357dbeaab2c8d247776424c51c25a`

This file preserves notebook markdown for semantic recovery. It excludes code-cell outputs.

<!-- cell 1 -->

This notebook implements phase 2 of the project, which involves compiling the network flow model and running a number of scenarios to simulate different policy options.

<!-- cell 2 -->

# Initialization code

<!-- cell 5 -->

# Introduction

<!-- cell 6 -->

The MERIS database (compiled by experts staff at BMMB) contains all the data needed to estimate unit value-creation-potential of any standing tree stem in Quebec. For each forest management unit in Quebec, BFEC staff compile and solve a Woodstock model to determing species-wise AAC. We want to be able to map value-creation--potential data from the MERIS database to each component (i.e. action) in any BFEC Woodstock solution. Data in BFEC Woodstock models is more hightly aggregated than data in the BMMB MERIS database. For example, a harvesting action in a Woodstock model will specify the development type, the action type, and the treated area. Applying this action will generate an array of outputs, including net merchantable harvest volume (expressed in $m^3$, by species group). There are 11 standard species groups in BFEC Woodstock models. The MERIS database stores value data using several different levels of aggregation. 

The finest level of data aggregation used in the MERIS database (for the data we need) expresses value-creation-potential of one unit (in $m^3$) of fibre in terms of a combination of one of 2 processor profiles (hardwood, softwood), 45 species codes, 26 stem size classes, and 6 product classes. In step 1 of phase 1 of this project, we compiled _disaggregation coefficients_ that can be used to disaggregate (i.e. explode) each unit of harvested volume output from a BFEC Woodstock model solution into smaller volume sub-units. Each volume sub-unit exactly matches the finest aggregation level used in the MERIS database, thus allowing us to retrofit MERIS value-creation-potential data to BFEC Woodstock model solutions.

In this context, the _value_ of the stem of a standing tree is defined from the perspective of a network of primary processing facilities seeking to maximize profit from sale of primary products (and primary co-products) to external markets. Given this definition of value, the unit value-creation--potential is equivalent to the sum of unit costs and revenues along a given stem's trajectory from standing tree to delivered primary product. 

We designed a linear programming (LP) optimisation model formulation that can emulate profit-maximizing fibre consumption behaviour of any hypothetical network of primary fibre processing facilities. We use a network flow model design pattern: a directed graph of processing nodes connected by transportation arcs. Fibre flows through the network from _source_ (`src`) nodes to _sink_ (`snk`) nodes. We model three additional layers of nodes between source and sink nodes (_viz._, _dispatch_ (`dsp`), _commodity_ (`cdt`), and _processor_ (`prc`) nodes), for a total of five layers of nodes. 

We model a 1:1 mapping between source nodes and harvesting decisions in the BFEC Woodstock model. In step 2 of phase 1, we retrofitted data from the MERIS database into an instance of our `ws3.woodstock.WoodstockModel` class, which allows us now to programatically interface the `WoodstockModel` object with the network flow model. There is a 1:1 mapping between source nodes and dispatch nodes. The number of source and dispatch nodes will vary, depending on the number of harvest actions in the simulated BFEC Woodstock model solution. Each dispatch node has one inbound arc. Flow along source-dispatch arcs represents _harvested area_ (measured in hectares). By setting appropriate upper and lower flow capacity bounds on these arcs, we can simulate harvesting and consumption of any subset of the Woodstock solution. In step 2 of phase 1, we compiled an `nvcp` (net value-creation-potential) coefficient for each harvesting decision in the Woodstock model. An `nvcp` coefficient represents the sum of all revenues and costs, from standing tree to delivered primary product, expressed on the basis of harvested area. The objective function value of our network flow model is defined as the scalar product of source-dispatch flow and net value-creation-potential coefficient vectors. 

Each dispatch node has one outbound arc connecting it to each of 12 commodity nodes. The notion of _commodity_ is used in the MERIS database as an intermediate aggregation scheme combining species group and product class. Outbound flow from dispatch nodes represents _harvested volume_ (measured in cubic metres). Thus, dispatch nodes convert harvested area commodity volumes. Unit stumpage cost in the MERIS database is defined as a function of stumpage zone and commodity, so we can map coefficients to dispatch-commodity flows representing unit stumpage cost.

Commodity nodes have one inbound arc for each dispatch node, and one outbound arc for each processing node. Outbound flows represent volume of merchantable fibre (in cubic metres). The number of processing nodes will vary from one instance to another, depending on the number of fibre processing facilities modelled. For our test dataset, we model fibre flows from management unit UA 064-51. We used publicly-available government documentation of timber licence (TL) agreements to compile the list of processing facilities that procure fibre directly from the target management unit. Since 2013, TL volumes in Quebec are attributed on a regional basis (i.e. for a set of management units). Our test dataset models fibre flows from a single management unit, so we need to disaggregate regional TL volumes to define appropriate upper bounds on flows between commodity and processing nodes. We use the most recent pre-2013 (management-unit-wise) TL volume data, for the set of management units in the target region, to estimate commodity-wise proportion of regional TL volumes corresponding to our target management unit, which we use as upper bounds on commodity-processor arc flows in our network model. Sawmill processing nodes generate a significant volume of chips as a co-product. For each sawmill processor node, we add one outbound arc for each pulpmill processor node. 

We model one sink node for each processor node, with matching arcs. Facility capacity constraints can be defined as flow bounds on these arcs.

<!-- cell 7 -->

# Load and compile data

<!-- cell 8 -->

In this section, we load and compile input data for our test case. We use data compiled in phase 1 of this project, for UA 064-51 (located in region 15).

<!-- cell 9 -->

First, we load the `WoodstockModel` object for UA 064-51 that we compiled in phase 1.

<!-- cell 11 -->

Define a helper function to perform element-wise sum of two lists of numbers.

<!-- cell 13 -->

Define a helper funciton to compile management area data we need to model source nodes.

<!-- cell 15 -->

Compile management area data. Calculate total available harvest area.

<!-- cell 17 -->

Define commodity labels and commodity aggregates.

<!-- cell 20 -->

Load UA-wise GA volume data for region 15 (compiled in phase 1). Compile list of commodities potentially processed for each mill.

<!-- cell 22 -->

Compile commodity-mill-wise GA volumes for UA 064-51. These will be used lated to set upper bounds on commodity-processor arc flows.

<!-- cell 24 -->

# Define the network flow model

<!-- cell 25 -->

Version 42.03 of network flow model. Added secondary hardwood chip flows from hardwood sawmill `prc` nodes to hardwood pulpwood `prc` nodes (i.e., Fortress, Domtar). For now, assuming that 100% of hardwood chips generated at hardwood sawmills are redirected to hardwood pulpwood processors, but set this up for as mill-wise parameters (so easy to modify).

From conversation with Louis-Marie Girard (Bois Nobles Ka'N'Enda Ltd, Scierie Mont-Laurier), tree-length logs are bucked at the mill. Pulp logs are sent directly to Domtar. Sawmilling process generates chips and sawdust. Chips are sent to Fortress and Domtar. Sawdust is sent to Uniboard. No shavings are generated, as there is no planing (lumber sold rough). No chips or sawdust are consumed by the mill, as there is no drying (lumber sold green). Not sure is this is representative of other hardwood sawmills in the area.

<!-- cell 30 -->

# Define helper functions

<!-- cell 31 -->

Define help functions for doing stuff like copying models, compiling value coefficients, running the model, plotting results, etc. We will use these helper functions later to keep the code that defines and runs scenarios (relatively) compact and readable.

<!-- cell 58 -->

# Scenarios

<!-- cell 59 -->

This section simulates a number of scenarios, using the network flow model and helper functions implemented above.

<!-- cell 60 -->

First, we output some data comparing AAC (from the BFEC Woodstock model) to GA volumes (i.e. timber licences attributed to mills for the same forest).

<!-- cell 62 -->

We see that a smaller proportion of the _tritf_ (hardwood pulpwood) commodity aggregate is attributed, compared the other two commodity aggregates. This is consistent with other sources of data comparing AAC and harvest volumes.

<!-- cell 63 -->

## Scenario 0
Solve the base model.

<!-- cell 66 -->

## Scenario 1
No capacity constraints. Solve model for a range of harvested area values between 0 and 100% of available area. $Z^*$ peaks when all positively-valued area is harvested.

<!-- cell 71 -->

## Scenario 2
Scenario 1, with volume-based subsidies.

<!-- cell 72 -->

### Scenario 2.1

<!-- cell 73 -->

Scenario 2.1 subsidy amounts are based on Belzile and Riopel (2015).

<!-- cell 78 -->

### Scenario 2.2

<!-- cell 79 -->

Based on scenario 2.1.

<!-- cell 80 -->

What if we added a 7 $\$/m^3$ subsidy on all volume flowing out of the `cdt_sepm` node? The CIFQ has been lobbying for a similar cost cut for softwood fibre lately.

<!-- cell 83 -->

### Scenario 2.3

<!-- cell 84 -->

Next we determine the minimal SEPM subsidy required so that the softwood commodity stream breaks even.

<!-- cell 87 -->

The SEPM commodity (almost) breaks even with a \$12.90 subsidy. However, only 94% of available area is harvested. Lets try to find the minimal SEPM subsidy required to induce harvesting of all available area.

<!-- cell 89 -->

Thus, we must increase the SEPM subsidy from \$12.90 to \$13.30 to induce harvesting of the last 6% of available area.

<!-- cell 90 -->

Next, we run the model based on scenario 2.1 parameteres, with \$12.90 SEPM subsidy, and plot results.

<!-- cell 94 -->

## Scenario 3

<!-- cell 95 -->

Based on scenario 2.1.

<!-- cell 96 -->

## Scenario 3.1

<!-- cell 97 -->

What if we omit the rente from scenario 2.1?

<!-- cell 100 -->

### Scenario 3.2

<!-- cell 101 -->

Builds on scenario 3.1. 

SEPM VPC is negative in scenario 3.1.What if we add the SEPM subsidy from scenario 2.3?

<!-- cell 104 -->

## Scenario 4

<!-- cell 105 -->

Scenario 1, with area-based subsidies. Subsidies on a harvested area basis, amounts based on standard BMMB MAM policy.

<!-- cell 106 -->

BMMB subsidies are defined on a harvested area basis. In the case of multiple destinations, subsidies are pro-rated based on proportion of volume by commodity. The formula for the subsidy $s$ is

$$
s = \sum_{i \in I} 20 (0.083d_i - 20) p_i
$$

where $I$ is the set of destinations for hardwood pulpwood, $d_i$ is the shortest-path driving distance (in kilometers) from the centroid of a stumpage zone to destination $i$, and $p_i$ is the proportion of hardwood pulpwood volume delivered to destination $i$.

Note that the subsidy is capped at 240 \$ per hectare, and is only applicable to partial cuts (less than 50\% BA removal) in hardwood and mixtedwood cover types. Stands where poplar dominates hardwood BA are not eligible.

<!-- cell 107 -->

We used Google Maps to estimate the shortest-path driving distances from the centroid of stumpage zone 661 to Thurso (Fortress mill, 242 km) and Windsor (Domtar mill, 447 km).

<!-- cell 112 -->

## Plot scenarios [1, 2, 3] side-by-side

<!-- cell 122 -->

## Scenario 5

<!-- cell 123 -->

Build on scenario 1.

<!-- cell 124 -->

### Scenario 5.1

<!-- cell 125 -->

Set UB for `cdtprc` flow variables to GA volumes (no subsidies).

<!-- cell 128 -->

### Scenario 5.2

<!-- cell 129 -->

Set LB for `cdtprc` flow variables to GA volumes.

<!-- cell 133 -->

Disable dual reductions in Gurobi presolve and re-optimize to confirm that the model is infeasible (Gurobi status code 3).

<!-- cell 136 -->

Infeasible. It is not possible to minimally meet all GA contract demands, even if we harvested all the available area. Conclusion: GA contracts are not mathematically compatible with the BFEC Woodstock model (i.e., we would need more, or different harvest area to minimally meet all GA contracts).

We can use a `Model.feasRelaxS()` to find minimal relaxation of GA LB constraints needed to restore feasibility.

<!-- cell 137 -->

TO DO: Use `Model.feasRelax` instead (can specify which variable bounds may be relaxed).

<!-- cell 141 -->

### Scenario 5.3

<!-- cell 142 -->

Re-implement scenario 5.2 as a goal programming model. Define a piecewise-linear objective for the `cdtprc` flow variables (constant unit incentive values on volume below GA, neutral for volume above GA). This is equivalent to GA as LB on flow.
