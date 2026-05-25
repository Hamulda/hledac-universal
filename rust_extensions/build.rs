fn main() {
    println!("cargo:rustc-link-search=framework=/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/lib");
    println!("cargo:rustc-env=RUST_TARGET=aarch64-apple-darwin");
    println!("cargo:rerun-if-changed=build.rs");
}
