FROM python:3.9

WORKDIR .

COPY . .
#RUN apt-get update && apt-get install -y ffmpeg


RUN apt-get update && apt-get install -y git
RUN pip3 install -r requirements.txt

CMD ["start.sh"]

ENTRYPOINT ["bash"]
