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
}

export interface RagFlowConfigSummary {
    api_url: string;
    api_key_configured: boolean;
    configured: boolean;
    metadata_provider?: string;
    knowledge_base_enabled?: boolean;
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
        axios.get<{code: number, data: RagFlowConfigSummary}>('/api/portal/ragflow/config')
};
