from ai_checks.adapters.embedding_onnx import BgeM3OnnxEmbedding

emb = BgeM3OnnxEmbedding()
result = emb.embed_texts(['проверка связи'])
print(f'вектор: {len(result[0])} измерений')
norm = sum(v * v for v in result[0]) ** 0.5
print(f'L2-норма: {norm:.6f}')
