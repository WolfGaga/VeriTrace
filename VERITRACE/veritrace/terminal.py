import argparse
import logging

from veritrace.agent import VeritraceAgent

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def parse_arguments():
    parser = argparse.ArgumentParser(description="VERITRACE Terminal CLI")
    parser.add_argument("--lang", default="cn", type=str, help="Language (en/cn)")
    parser.add_argument(
        "--llm-provider", default="siliconflow", type=str, help="LLM provider (siliconflow, openai, anthropic, qwen)"
    )
    parser.add_argument("--llm-model", default="Qwen/Qwen2.5-7B-Instruct", type=str, help="LLM model identifier")
    parser.add_argument("--llm-api-key", default=None, type=str, help="LLM API key")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main():
    args = parse_arguments()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print(f"Initializing VERITRACE Agent ({args.llm_provider}: {args.llm_model})...")

    try:
        agent = VeritraceAgent(
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_api_key=args.llm_api_key,
            language=args.lang,
        )
    except Exception as e:
        print(f"Error initializing agent: {e}")
        return

    print("VERITRACE Agent ready. Type 'exit' or 'quit' to stop.")

    while True:
        try:
            user_input = input("\nUser: ")
            if user_input.lower() in ["exit", "quit"]:
                break

            if not user_input.strip():
                continue

            print("\nVERITRACE: ", end="", flush=True)

            for event in agent(user_input):
                content = event.data.get("article") or event.data.get("content")
                if content:
                    print(f"\n[{event.type}]: {content}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
