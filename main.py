import customtkinter as ctk
from ui.main_window import MainWindow

ctk.set_appearance_mode("dark")


def main() -> None:
    root = ctk.CTk()
    root.title("Appdesk")
    root.resizable(False, False)
    app = MainWindow(root)
    app.pack()
    root.mainloop()


if __name__ == "__main__":
    main()
