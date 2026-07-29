import argparse
import json


def summarize(path):
    records = json.loads(path.read_text())
    return {"count": len(records)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=__import__("pathlib").Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.path)))


if __name__ == "__main__":
    main()
