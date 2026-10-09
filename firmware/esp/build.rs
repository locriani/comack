fn main() {
    if !std::path::Path::new("src/secrets.rs").exists() {
        panic!(
            "firmware/esp/src/secrets.rs is missing: copy src/secrets.rs.example and fill it in"
        );
    }
    println!("cargo:rerun-if-changed=src/secrets.rs");
    embuild::espidf::sysenv::output();
}
