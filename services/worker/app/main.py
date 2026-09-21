import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Cusp worker...")
    # will be adding actual bg jobs here

if __name__ == "__main__":
    main()