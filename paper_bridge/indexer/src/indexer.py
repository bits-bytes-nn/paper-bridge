# # Initialize graph and vector stores
#         self.graph_store = GraphStoreFactory.for_graph_store(graph_store_url)
#         self.vector_store = VectorStoreFactory.for_vector_store(vector_store_url)
#         self.graph_index = LexicalGraphIndex(self.graph_store, self.vector_store)


#     def process_papers(self, papers_by_date: Dict[str, List[Paper]]) -> None:
#         """
#         Download PDFs and index paper content
#         """
#         for date_papers in papers_by_date.values():
#             for paper in date_papers:
#                 try:
#                     # Get arxiv ID from paper ID
#                     arxiv_id = paper.id.split("/")[-1]

#                     # Download PDF using arxiv API
#                     search = arxiv.Search(id_list=[arxiv_id])
#                     paper_obj = next(search.results())
#                     paper_obj.download_pdf()

#                     # Read PDF content
#                     pdf_path = f"{arxiv_id}.pdf"
#                     reader = PDFReader()
#                     docs = reader.load_data(pdf_path)

#                     # Extract main content (exclude abstract and references)
#                     main_content = self.extract_main_content(docs)

#                     # Index content using GraphRAG
#                     self.graph_index.extract_and_build(
#                         main_content,
#                         metadata={"paper_id": paper.id, "title": paper.title},
#                         show_progress=True,
#                     )

#                     # Cleanup
#                     os.remove(pdf_path)

#                 except Exception as e:
#                     print(f"Error processing paper {paper.id}: {e}")
#                     continue


#     def extract_main_content(self, docs: List[Any]) -> List[Any]:
#         """
#         Extract main content from paper, excluding abstract and references
#         """
#         # Simple heuristic - remove first page (usually abstract)
#         # and last few pages (usually references)
#         if len(docs) <= 2:
#             return docs
#         return docs[1:-2]


#     def format_paper(self, paper: Paper):
#         """
#         Format paper data into a clean dictionary
#         """
#         return {
#             "title": paper.title,
#             "paper_id": paper.id,
#             "authors": [author.name for author in paper.authors],
#             "upvotes": paper.upvotes,
#             "comments": paper.num_comments or 0,
#             "published_at": paper.published_at.isoformat(),
#             "url": f"https://huggingface.co/papers/{paper.id}",
#         }
