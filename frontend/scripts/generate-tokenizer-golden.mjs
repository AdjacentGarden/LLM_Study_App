/** Rebuild query-token goldens with the pinned source tokenizer, without inference. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = process.env.BOOKCOURSE_SOURCE;
if (!source) throw new Error('Set BOOKCOURSE_SOURCE to the read-only original frontend repository.');
const require = createRequire(path.join(source, 'package.json'));
let packageRoot = path.dirname(require.resolve('@huggingface/transformers'));
while (packageRoot !== path.dirname(packageRoot)) {
  try { if (JSON.parse(await fs.readFile(path.join(packageRoot, 'package.json'), 'utf8')).name === '@huggingface/transformers') break; } catch {}
  packageRoot = path.dirname(packageRoot);
}
const packageFile = path.join(packageRoot, 'package.json');
const { AutoTokenizer } = await import(pathToFileURL(path.join(packageRoot, 'src/tokenizers.js')).href);
const { env } = await import(pathToFileURL(path.join(packageRoot, 'src/env.js')).href);
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.useBrowserCache = false;
env.useFSCache = false;
const modelDirectory = path.join(root, 'Resources/Content/rag/models/Xenova/bge-small-zh-v1.5');
const corpusDirectory = path.join(root, 'Resources/Content/rag/biology-required-2-rag-v1');
const readJSON = async file => JSON.parse(await fs.readFile(file, 'utf8'));
const [manifest, evaluation, tokenizerDocument, packageJSON] = await Promise.all([
  readJSON(path.join(corpusDirectory, 'manifest.json')),
  readJSON(path.join(corpusDirectory, 'evaluation-set.json')),
  readJSON(path.join(modelDirectory, 'tokenizer.json')),
  readJSON(packageFile)
]);
if (tokenizerDocument.normalizer.lowercase !== false) throw new Error('Expected cased BGE BertNormalizer.');
const tokenizer = await AutoTokenizer.from_pretrained(modelDirectory, { local_files_only: true });
const cases = evaluation.queries.map(item => ({ id: item.id, text: manifest.embeddings.query_prefix + item.query, max_length: 512 }));
cases.push(
  { id: 'empty', text: '', max_length: 512 },
  { id: 'latin-cased', text: 'DNA dna mRNA MRNA Mrna ATGC atgc', max_length: 512 },
  { id: 'accent-preservation', text: 'HÉLLO café Cafe\u0301 coöperate résumé', max_length: 512 },
  { id: 'whitespace', text: '减数\n分裂\tDNA\r复制　配子\u00a0的特点', max_length: 512 },
  { id: 'special-tokens', text: '[CLS] DNA [MASK] [SEP] [PAD] [UNK]', max_length: 512 },
  { id: 'punctuation', text: '基因（DNA）: A→T，1+2=3! 50% _test_ ＡＢＣ', max_length: 512 },
  { id: 'invalid-controls', text: 'DNA\u0000减数\u0007分裂\u200b染色体\ufffd', max_length: 512 },
  { id: 'emoji-and-cjk-extension', text: '细胞🧬遗传 𠀀染色体', max_length: 512 },
  { id: 'long-unknown-word', text: 'a'.repeat(101), max_length: 512 },
  { id: 'truncate-512', text: manifest.embeddings.query_prefix + '染色体的遗传规律'.repeat(90), max_length: 512 },
  { id: 'truncate-small', text: '为这个句子生成表示以用于检索相关文章：DNA复制', max_length: 8 }
);
for (const item of cases) {
  // return_tensor:false avoids even constructing an ONNX Tensor; no model or worker is started.
  const encoded = tokenizer(item.text, { add_special_tokens: true, truncation: true, max_length: item.max_length, padding: false, return_tensor: false });
  item.input_ids = encoded.input_ids.map(Number);
}
const document = {
  schema_version: 1,
  generator: `@huggingface/transformers ${packageJSON.version} AutoTokenizer`,
  model_id: manifest.embeddings.model_id,
  revision: manifest.embeddings.revision,
  normalizer: tokenizerDocument.normalizer,
  query_prefix: manifest.embeddings.query_prefix,
  cases
};
await fs.writeFile(path.join(root, 'Resources/Data/tokenizer-golden.json'), JSON.stringify(document, null, 2) + '\n');
console.log(`Generated ${cases.length} cased tokenizer golden cases; no inference or network access used.`);
