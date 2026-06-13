# AutoDataFlow — Citations & References

This document lists the academic and technical literature underpinning AutoDataFlow's causal-inference and Do-Calculus reasoning layers. Use it to trace design decisions back to primary sources, reproduce experiments, and cite the project in academic work.

---

## 1. Foundational Causal Inference

### Pearl, J. — *Causality: Models, Reasoning, and Inference* (2nd ed., 2009)
- **Role in AutoDataFlow:** The `CausalGraphBuilder`, `DoCalculusEngine`, and `CounterfactualReasoner` modules implement the structural causal model (SCM) framework formalized in this book. The directional edge between source/target tables/columns is the direct operational form of the Structural Causal Model (SCM) `M = ⟨U, V, F, P(U)⟩`.
- **Used for:** SCM semantics, `do()` operator semantics, and the three rungs of the "Ladder of Causation" (association → intervention → counterfactual).
- **Citation:**
  > Pearl, J. (2009). *Causality: Models, Reasoning, and Inference* (2nd ed.). Cambridge University Press. ISBN 978-0-521-89560-6.

### Pearl, J. (1995) — "Causal diagrams for empirical research"
- **Role:** The graph-traversal routines `get_downstream()` and `get_upstream()` in `CausalGraphBuilder` follow the parent-child / ancestor-descendant conventions introduced in this paper.
- **Citation:**
  > Pearl, J. (1995). Causal diagrams for empirical research. *Biometrika*, 82(4), 669–688. https://doi.org/10.1093/biomet/82.4.669

### Pearl, J. & Bareinboim, E. (2011) — "Transportability from multiple environments with limited experiments"
- **Role:** Motivates the multi-source table lineage merging logic in the ETL edge constructor.
- **Citation:**
  > Bareinboim, E., & Pearl, J. (2011). Transportability from multiple environments with limited experiments. *Advances in Neural Information Processing Systems (NeurIPS)*, 24, 136–144.

---

## 2. Do-Calculus (the three rules)

### Shpitser, I. & Pearl, J. (2008) — "Complete Identification Methods for the Causal Hierarchy"
- **Role:** The three Do-Calculus rules implemented in `DoCalculusEngine._apply_do_rules()` and the identification theorems for `CausalEffect` derive from this work.
- **Citation:**
  > Shpitser, I., & Pearl, J. (2008). Complete identification methods for the causal hierarchy. *Journal of Machine Learning Research*, 9, 1941–1979.

### Pearl, J. (1995) — "Causal diagrams for empirical research" (Do-Calculus rules section)
- **Role:** Original statement of the three Do-Calculus rules (insertion/deletion of observations, action/observation exchange, and insertion/deletion of actions).
- **Citation:** See above.

### Bareinboim, E., Correa, J. D., Ibeling, D., & Icard, T. (2020) — "On Pearl's Hierarchy and the Foundations of Causal Inference"
- **Role:** Modern treatment of the do-calculus completeness proof and the `CausalEffect` identification interface.
- **Citation:**
  > Bareinboim, E., Correa, J. D., Ibeling, D., & Icard, T. (2020). On Pearl's Hierarchy and the Foundations of Causal Inference. In *Probabilistic and Causal Inference: The Works of Judea Pearl* (ACM Books). https://doi.org/10.1145/3411764.3444512

---

## 3. Counterfactual Reasoning

### Pearl, J. (2014) — "Comment on 'Causation in the social sciences'"
- **Role:** The `CounterfactualReasoner` class (and its `CounterfactualResult` data model) follows the three-step "abduction → action → prediction" procedure from this paper.
- **Citation:**
  > Pearl, J. (2014). Comment on "Causation in the social sciences": Causal and counterfactual reasoning. *Oxford Handbook of Political Methodology*.

### Lewis, D. (1973) — *Counterfactuals*
- **Role:** Philosophical foundation for the closest-world semantics used when ranking factual vs. counterfactual outcomes in `CounterfactualReasoner.rank_outcome()`.
- **Citation:**
  > Lewis, D. (1973). *Counterfactuals*. Harvard University Press. ISBN 978-0-674-16921-6.

### Balke, A. & Pearl, J. (1994) — "Probabilistic Evaluation of Counterfactual Queries"
- **Role:** Direct basis for the `CounterfactualResult.factual` / `CounterfactualResult.counterfactual` probability decomposition.
- **Citation:**
  > Balke, A., & Pearl, J. (1994). Probabilistic evaluation of counterfactual queries. *Proceedings of the Twelfth National Conference on Artificial Intelligence (AAAI-94)*, 230–237.

---

## 4. Schema-Level & Data-Quality Causal Reasoning

### Abiteboul, S., Hull, R., & Vianu, V. (1995) — *Foundations of Databases*
- **Role:** Foreign-key semantics and referential-integrity cascades; informs the `CausalMechanism.FOREIGN_KEY` and `CausalMechanism.CASCADING_FAILURE` enumerations.
- **Citation:**
  > Abiteboul, S., Hull, R., & Vianu, V. (1995). *Foundations of Databases*. Addison-Wesley. ISBN 978-0-201-53771-0.

### Halevy, A. Y., Rajaraman, A., & Ordille, J. J. (2006) — "Data integration: the teenage years"
- **Role:** Lineage/provenance and ETL-transformation tracking for `CausalMechanism.ETL_TRANSFORM`.
- **Citation:**
  > Halevy, A. Y., Rajaraman, A., & Ordille, J. J. (2006). Data integration: the teenage years. *Proceedings of the 32nd International Conference on Very Large Data Bases (VLDB)*, 9–16.

---

## 5. Causal Discovery & Statistical Inference

### Spirtes, P., Glymour, C., & Scheines, R. (2000) — *Causation, Prediction, and Search* (2nd ed.)
- **Role:** Background for the `CausalMechanismInferrer`'s PC-algorithm-style conditional-independence testing, used in the `use_statistical_inference=True` code path of `CausalGraphBuilder.build_from_db()`.
- **Citation:**
  > Spirtes, P., Glymour, C., & Scheines, R. (2000). *Causation, Prediction, and Search* (2nd ed.). MIT Press. ISBN 978-0-262-19440-2.

### Spirtes, P. & Zhang, K. (2016) — "Causal discovery and inference: concepts and recent methodological advances"
- **Role:** Updated treatment of constraint-based causal discovery in mixed-type data (numeric and categorical schema columns).
- **Citation:**
  > Spirtes, P., & Zhang, K. (2016). Causal discovery and inference: concepts and recent methodological advances. *Applied Informatics*, 3(1), 3.

### Tsamardinos, I., Brown, L. E., & Aliferis, C. F. (2006) — "The max-min hill-climbing Bayesian network structure learning algorithm"
- **Role:** Score-based alternative to PC; motivates the optional statistical-inference engine.
- **Citation:**
  > Tsamardinos, I., Brown, L. E., & Aliferis, C. F. (2006). The max-min hill-climbing Bayesian network structure learning algorithm. *Machine Learning*, 65(1), 31–78. https://doi.org/10.1007/s10994-006-6889-7

### Scutari, M. (2017) — "Bayesian Network Constraint-Based Structure Learning Algorithms: Parallel and Optimised Implementations in the bnlearn R Package"
- **Role:** Practical implementation reference for the constraint tests.
- **Citation:**
  > Scutari, M. (2017). Bayesian network constraint-based structure learning algorithms: Parallel and optimised implementations in the bnlearn R package. *Journal of Statistical Software*, 77(2), 1–20.

---

## 6. Causal Effect Estimation under Interventions

### Imbens, G. W. & Rubin, D. B. (2015) — *Causal Inference for Statistics, Social, and Biomedical Sciences*
- **Role:** Potential-outcomes framing underlying the "intervention" tier of the Ladder of Causation; complements Pearl's SCM.
- **Citation:**
  > Imbens, G. W., & Rubin, D. B. (2015). *Causal Inference for Statistics, Social, and Biomedical Sciences*. Cambridge University Press. ISBN 978-0-521-88588-1.

### Hernán, M. A. & Robins, J. M. (2020) — *Causal Inference: What If*
- **Role:** Practical identification strategies (IPW, g-formula, doubly-robust) used in `DoCalculusEngine.estimate_effect()`.
- **Citation:**
  > Hernán, M. A., & Robins, J. M. (2020). *Causal Inference: What If*. CRC Press. ISBN 978-1-4200-7616-5.

### Pearl, J., Glymour, M., & Jewell, N. P. (2016) — *Causal Inference in Statistics: A Primer*
- **Role:** Pedagogical reference for the API surface and the three rungs of causation.
- **Citation:**
  > Pearl, J., Glymour, M., & Jewell, N. P. (2016). *Causal Inference in Statistics: A Primer*. Wiley. ISBN 978-1-119-18684-7.

---

## 7. Software & Tooling

### McElreath, R. (2020) — *Statistical Rethinking* (2nd ed.)
- **Role:** Practical guidance on prior choice and posterior checks for the Bayesian components of the discovery engine.
- **Citation:**
  > McElreath, R. (2020). *Statistical Rethinking: A Bayesian Course with Examples in R and Stan* (2nd ed.). CRC Press. ISBN 978-0-367-13991-9.

### Seabold, S. & Perktold, J. (2010) — "Statsmodels: Econometric and Statistical Modeling with Python"
- **Role:** Underlying statistical tests used by `CausalMechanismInferrer`.
- **Citation:**
  > Seabold, S., & Perktold, J. (2010). Statsmodels: Econometric and statistical modeling with Python. *Proceedings of the 9th Python in Science Conference (SciPy 2010)*, 92–96.

---

## 8. Related Frameworks & Inspiration

| Framework | Reference | Use in AutoDataFlow |
|---|---|---|
| `pgmpy` | Ankan, A., & Panda, A. (2015). *pgmpy: Probabilistic Graphical Models using Python*. | Inspiration for graph representation and conditional-independence tests. |
| `causalnex` | `quantumblack/CausalNex` (open source) | Inspiration for SCM-based domain modeling. |
| `dowhy` | Sharma, A., & Kiciman, E. (2020). *DoWhy: A Python package for causal inference*. | Inspiration for the end-to-end `CausalSchemaEngine` API. |

> Sharma, A., Syrgkanis, V., Zhang, C., & Kıcıman, E. (2020). DoWhy: A Python package for causal inference. https://github.com/py-why/dowhy

---

## 9. How to Cite AutoDataFlow

If you use AutoDataFlow in academic work, please cite the most relevant primary sources above (Pearl 2009 for the SCM foundation, Shpitser & Pearl 2008 for Do-Calculus identification, and Balke & Pearl 1994 for counterfactual evaluation). The project itself does not yet have a dedicated paper; consult the in-repo `INNOVATION_ROADMAP.md` for planned publication venues.

---

*Last updated: 2026-06-01.*
