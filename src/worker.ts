export default {
	async fetch(_request: Request): Promise<Response> {
		return new Response("memory-mcp worker ok", { status: 200 });
	},
};

