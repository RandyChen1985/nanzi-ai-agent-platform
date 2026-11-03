import axios from '@/utils/axios';

export interface RagFlowAgent {
    id: string;
    title: string;
    description?: string;
    avatar?: string;
    update_time?: number;
}

export interface RagFlowDataset {
    id: string;
    ragflow_dataset_id?: string;
    name: string;
    platform_name?: string;
    description?: string;
    platform_description?: string;
    avatar?: string;
    permission?: string;
    doc_count?: number;
    document_count?: number; // RAGFlow 列表接口实际字段名
    chunk_count?: number;
    update_time?: number | string;
    created_by?: string;
    can_write?: boolean;
    can_view_chunks?: boolean;
    is_read_only?: boolean;
    /** 可见范围：public（需授权）/ team（部门只读）/ private（仅创建人） */
    visibility?: string;
}

export interface RagFlowConfigSummary {
    api_url: string;
    api_key_configured: boolean;
    configured: boolean;
    metadata_provider?: string;
    knowledge_base_enabled?: boolean;
    /** 检索测试页的参数默认值，来自系统配置 knowledge_ragflow_* */
    retrieval_defaults?: {
        top_n: number;
        similarity_threshold: number;
        vector_similarity_weight: number;
    };
}

export const ragflowApi = {
    listAgents: (page = 1, pageSize = 100, overrideUrl?: string, overrideKey?: string) => 
        axios.get<{code: number, data: RagFlowAgent[]}>('/api/portal/ragflow/agents', { 
            params: { page, page_size: pageSize, override_url: overrideUrl, override_key: overrideKey },
            timeout: 5000 // 5s timeout for external service
        }),
        
    listDatasets: (page = 1, pageSize = 100, overrideUrl?: string, overrideKey?: string, includeMissing?: boolean) =>
        axios.get<{code: number, data: RagFlowDataset[]}>('/api/portal/ragflow/datasets', { 
            params: { 
                page, 
                page_size: pageSize, 
                override_url: overrideUrl, 
                override_key: overrideKey,
                include_missing: includeMissing
            },
            timeout: 5000 
        }),

    getConfig: () =>
        axios.get<{code: number, data: RagFlowConfigSummary}>('/api/portal/ragflow/config'),

    /** 图片转来的 md 才有原图：传 md 文件名，后端按哈希尾巴反查图片文件。 */
    getImageSourceInfo: (datasetId: string, mdName: string) =>
        axios.get<{
            code: number;
            data: { exists: boolean; name?: string; size?: number; content_type?: string; url?: string };
        }>(`/api/portal/ragflow/datasets/${encodeURIComponent(datasetId)}/images/source-info/${encodeURIComponent(mdName)}`, {
            timeout: 5000,
        })
};
