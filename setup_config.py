from tracker_app import main

if __name__ == "__main__":
    import sys
    sys.argv = [sys.argv[0], "--setup"]
    main()
