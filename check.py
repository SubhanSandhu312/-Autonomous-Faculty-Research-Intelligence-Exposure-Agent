import chroma_db

client = chroma_db.PersistentClient(path="chroma_db")

collection = client.get_collection("publications")

print(collection.get())