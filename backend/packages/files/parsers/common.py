import os
import re
import tempfile
import time

import nest_asyncio
import tiktoken
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from llama_parse import LlamaParse
from logger import get_logger
from models import File
from modules.brain.service.brain_vector_service import BrainVectorService
from modules.upload.service.upload_file import DocumentSerializable
from packages.embeddings.vectors import Neurons

nest_asyncio.apply()

logger = get_logger(__name__)


async def process_file(
    file: File,
    loader_class,
    brain_id,
    original_file_name,
    integration=None,
    integration_link=None,
):
    dateshort = time.strftime("%Y%m%d")
    neurons = Neurons()

    if os.getenv("LLAMA_CLOUD_API_KEY"):
        doc = file.file
        document_ext = os.path.splitext(doc.filename)[1]
        if document_ext in [".pdf", ".docx", ".doc"]:
            document_tmp = tempfile.NamedTemporaryFile(
                suffix=document_ext, delete=False
            )
            # Seek to the beginning of the file
            doc.file.seek(0)
            document_tmp.write(doc.file.read())

            parser = LlamaParse(
                result_type="markdown",  # "markdown" and "text" are available
                parsing_instruction="Try to extract the tables and checkboxes. Transform tables to key = value. You can duplicates Keys if needed. For example: Productions Fonts = 300 productions Fonts Company Desktop License = Yes for Maximum of 60 Licensed Desktop users For example checkboxes should be: Premium Activated = Yes License Premier = No If a checkbox is present for a table with multiple options.  Say Yes for the one activated and no for the one not activated",
            )

            document_llama_parsed = parser.load_data(document_tmp.name)
            document_tmp.close()
            document_to_langchain = document_llama_parsed[0].to_langchain_format()
            text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                chunk_size=file.chunk_size, chunk_overlap=file.chunk_overlap
            )
            document_to_langchain = Document(
                page_content=document_to_langchain.page_content
            )
            file.documents = text_splitter.split_documents([document_to_langchain])
    else:

        file.compute_documents(loader_class)

    metadata = {
        "file_sha1": file.file_sha1,
        "file_size": file.file_size,
        "file_name": file.file_name,
        "chunk_size": file.chunk_size,
        "chunk_overlap": file.chunk_overlap,
        "date": dateshort,
        "original_file_name": original_file_name or file.file_name,
        "integration": integration or "",
        "integration_link": integration_link or "",
    }
    docs = []

    enc = tiktoken.get_encoding("cl100k_base")

    if file.documents is not None:
        logger.info("Coming here?")
        for doc in file.documents:  # pyright: ignore reportPrivateUsage=none
            new_metadata = metadata.copy()
            logger.info(f"Processing document {doc}")
            # Add filename at beginning of page content
            doc.page_content = f"Filename: {new_metadata['original_file_name']} Content: {doc.page_content}"

            doc.page_content = doc.page_content.replace("\u0000", "")
            # Replace unsupported Unicode characters
            doc.page_content = re.sub(r"[^\x00-\x7F]+", " ", doc.page_content)

            len_chunk = len(enc.encode(doc.page_content))

            # Ensure the text is in UTF-8
            doc.page_content = doc.page_content.encode("utf-8", "replace").decode(
                "utf-8"
            )

            new_metadata["chunk_size"] = len_chunk
            doc_with_metadata = DocumentSerializable(
                page_content=doc.page_content, metadata=new_metadata
            )
            docs.append(doc_with_metadata)

    created_vector = neurons.create_vector(docs)

    brain_vector_service = BrainVectorService(brain_id)
    for created_vector_id in created_vector:
        result = brain_vector_service.create_brain_vector(
            created_vector_id, metadata["file_sha1"]
        )
        logger.debug(f"Brain vector created: {result}")

    if created_vector:
        return len(created_vector)
    else:
        return 0
