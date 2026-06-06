# syntax=docker/dockerfile:1
FROM public.ecr.aws/lambda/python:3.12

# Install only what the extraction Lambda actually uses — keeps the
# image under Lambda's 10 GB image size cap with plenty of headroom.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the knowledge_graph_agent package and the handler module into ${LAMBDA_TASK_ROOT}.
COPY knowledge_graph_agent/ ${LAMBDA_TASK_ROOT}/knowledge_graph_agent/
COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.lambda_handler"]
