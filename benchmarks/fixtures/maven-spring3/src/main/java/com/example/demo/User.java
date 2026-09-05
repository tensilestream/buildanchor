package com.example.demo;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class User {
    @Id private Long id;
    private String name;
}
