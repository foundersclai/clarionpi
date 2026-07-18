# Workshop source charter

The Workshop MVP exercises the standard ClarionPI workflow using deterministic demonstration
scenarios. It is an R1 product-evidence surface, not a substitute for real-world release evidence.

Only owned-synthetic scenario inputs are allowed.

Do not use `samples/`, tests, or real case records as Workshop scenario inputs. Do not derive a
scenario from any live client matter, even after informal redaction. Synthetic sources must be
owned for this purpose and must disclose their demonstration identity throughout the workflow and
in generated artifacts.

Workshop evidence cannot close the legal, PHI, ethics, or live-pilot gates for R2.

The standard domain services, attorney gates, fact registry, money engine, validators, package
builder, artifact store, and provenance routes remain authoritative. Workshop tooling may control
scenario lifecycle and disclosure; it may not create a parallel workflow, seed downstream rows,
weaken a guard, auto-approve a gate, or introduce a Workshop-specific legal conclusion.
