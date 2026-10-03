INSERT INTO system_configs (`key`, `value`, `description`, `category`, `is_secret`)
VALUES 
('ragflow_similarity_threshold', '0.4', 'RAGFlow 语义检索的最低相似度阈值 (0-1)，越低召回率越高但越不精准', 'metadata', 0),
('ragflow_vector_weight', '0.85', 'RAGFlow 混合检索中向量检索的权重 (0-1)，剩余为全文检索权重', 'metadata', 0)
ON DUPLICATE KEY UPDATE 
`description` = VALUES(`description`),
`category` = VALUES(`category`);
