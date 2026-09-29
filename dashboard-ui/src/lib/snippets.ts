export function curlSnippet(base: string, requireKey: boolean, model = 'YOUR_MODEL'): string {
  const body = `'{"model": "${model}", "messages": [{"role": "user", "content": "Hello"}]}'`;
  return requireKey
    ? `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer $JANUS_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d ${body}`
    : `curl ${base}/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -d ${body}`;
}
