# Near-5-year guokao tag weights and knowledge map - Reflection

- Section 8 now has a product-layer heat-map owner: `product.exam_tag_weights`.
- Section 8.5 now has a product-layer tag registry / learning-map owner: `product.knowledge_map_nodes`.
- The refresh logic treats `exam_year` as the year parsed from the paper title, with occurrence year only as fallback, because real guokao rows use calendar years that can lag the exam title year.
- Taskstream/practice no longer pick `判断推理` or `2026` internally; they ask the knowledge domain for a weighted guokao xingce practice selection.
- Practice selection consumes `published + text_supported + knowledge_point` map nodes, so module/question_type rows can exist for governance without dominating the execution path.
- Missing map/weight data is a hard error (`503` at route boundary), not a silent fallback.
- Practice selection excludes visual-required questions for the current execution path and uses recent guokao questions first, then older guokao questions with the same stable tag as the 30% supplement.
- `image_unsupported` map nodes are preserved for statistics/governance but must not enter method-card or external wrong-question memory loops automatically.
- Production materialization is complete for both derived tables: `product.exam_tag_weights` has 607 guokao/xingce rows, and `product.knowledge_map_nodes` has 2462 guokao/xingce rows.
- The map refresh is intentionally delete/rebuild only for generated derived rows; manually governed rows keep curated fields while derived weights/method-card links can still refresh.

Remaining follow-up is product work, not a hidden fallback: external wrong-question ingestion still needs an API that resolves against `product.knowledge_map_nodes`, stores candidate nodes and user confirmation, and rejects model-created free tags.

Method Pack output does not grant completion authority.
